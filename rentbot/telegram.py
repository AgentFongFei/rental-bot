"""Format listings and send them through the Telegram Bot API."""

from __future__ import annotations

import html
import time

import requests

from rentbot import orientation

_MAX_LEN = 4000  # Telegram caps a message at 4096 characters


def _yes(ok: bool) -> str:
    return "✅" if ok else "—"


def format_listing(n: int, x: dict) -> str:
    e = html.escape
    fees = x.get("extra_fees", 0)
    price = f"{x['price']:,}/月" + (f"（另有管理費/車位費約 {fees:,}）" if fees else "")
    if x.get("parking_fee_unknown"):
        price += "（車位費另計，金額未標示）"
    o = x.get("orientation") or {}
    community = x.get("community") or "591 未標社區名稱"
    if x.get("community") and not x.get("community_url"):
        community += "（刊登者自填）"
    return "\n".join([
        f"<b>{n}. {e(x['title'])}</b>",
        f"💰 {price}",
        "📍 " + "｜".join(e(v) for v in (
            x.get("district", ""), x.get("layout", ""),
            f"{x['area_ping']}坪" if x.get("area_ping") else "", x.get("floor", "")) if v),
        f"🚗 {x.get('parking') or '?'}車位  🛏 床{_yes('床' in x.get('facilities', set()))}  "
        f"🔥 瓦斯爐{_yes(x.get('gas_stove', False))}",
        f"🏢 社區：{e(community)}",
        f"🧭 大門：{e(orientation.describe(o.get('gate')))}",
        f"🅿️ 車道口：{e(orientation.describe(o.get('driveway')))}",
        f'🔗 <a href="{x["link"]}">591 物件頁</a>',
    ])


def build_messages(header: str, listings: list[dict]) -> list[str]:
    messages, current = [], header
    for i, x in enumerate(listings, 1):
        block = format_listing(i, x)
        if len(current) + len(block) + 2 > _MAX_LEN:
            messages.append(current)
            current = block
        else:
            current += "\n\n" + block
    messages.append(current)
    return messages


def send(token: str, chat_id: str, messages: list[str]) -> None:
    for text in messages:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=30,
        )
        r.raise_for_status()
        time.sleep(1)
