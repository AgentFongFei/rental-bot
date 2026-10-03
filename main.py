"""Weekly run: search 591, filter, look up orientation, send the top picks to Telegram.

Usage:
  python main.py                 # run and send to Telegram
  python main.py --dry-run       # run and print, don't send or mark as seen
  python main.py --dump URL FILE # save a rendered 591 page for debugging selectors
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import yaml

from rentbot import filters, orientation, telegram
from rentbot.scraper import Scraper, list_url

ROOT = Path(__file__).resolve().parent
SEEN_PATH = ROOT / "data" / "seen.json"
COUNTY = {7: "苗栗縣", 4: "新竹市"}


def load_seen() -> set[str]:
    try:
        return set(json.loads(SEEN_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def save_seen(seen: set[str]) -> None:
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(sorted(seen), indent=0) + "\n", encoding="utf-8")


def collect(cfg: dict, seen: set[str]) -> list[dict]:
    picks: list[dict] = []
    with Scraper(delay_seconds=cfg["request_delay_seconds"]) as s:
        for area in cfg["regions"]:
            for search in cfg["searches"]:
                url = list_url(area["region"], search["kind"], cfg["max_rent"], search.get("rooms"))
                found = s.search(url, cfg["max_pages_per_search"])
                print(f"[{COUNTY.get(area['region'])} {search['name']}] 列表 {len(found)} 筆")
                for x in found:
                    if x["id"] in seen or not filters.in_districts(x, area["districts"]):
                        continue
                    if x["price"] > cfg["max_rent"]:
                        continue
                    detail = s.detail(x)
                    if detail is None:
                        continue
                    x.update(detail)
                    x["kind_name"] = search["name"]
                    reasons = filters.check(x, cfg)
                    if reasons:
                        print(f"  ✗ {x['id']} {x['title'][:20]}：{'、'.join(reasons)}")
                        continue
                    x["gas_stove"] = filters.has_gas_stove(x)
                    x["orientation"] = orientation.resolve(x, COUNTY.get(area["region"], ""))
                    print(f"  ✓ {x['id']} {x['title'][:20]}")
                    picks.append(x)
    picks.sort(key=lambda x: filters.score(x, cfg))
    return picks[: cfg["weekly_count"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dump", nargs=2, metavar=("URL", "FILE"))
    args = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    if args.dump:
        with Scraper() as s:
            s.dump(*args.dump)
        return 0

    seen = load_seen()
    picks = collect(cfg, seen)
    header = f"🏠 <b>{date.today():%m/%d} 本週租屋推薦</b>（{len(picks)} 筆）"
    if not picks:
        header += "\n這週沒有新的符合條件物件。"
    messages = telegram.build_messages(header, picks)

    if args.dry_run:
        print("\n\n".join(messages))
        return 0

    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID", file=sys.stderr)
        return 1
    telegram.send(token, chat_id, messages)
    save_seen(seen | {x["id"] for x in picks})
    return 0


if __name__ == "__main__":
    sys.exit(main())
