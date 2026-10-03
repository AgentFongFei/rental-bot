"""Save rendered 591 pages (HTML + visible text) so selectors can be checked offline."""

import sys
from pathlib import Path

from rentbot.scraper import Scraper

PAGES = {
    "list_hsinchu_2room": "https://rent.591.com.tw/list?region=4&kind=1&price=0$_25000$&other=lift,cook&option=bed&layout=2",
    "list_miaoli_suite": "https://rent.591.com.tw/list?region=7&kind=2&price=0$_25000$&other=lift,cook&option=bed",
    "detail_flat_parking_orientation": "https://rent.591.com.tw/21990533",
    "detail_mechanical": "https://rent.591.com.tw/21958216",
    "detail_empty_unit": "https://rent.591.com.tw/22061290",
    "detail_suite_passed": "https://rent.591.com.tw/22082118",
}

out = Path(sys.argv[1] if len(sys.argv) > 1 else "debug")
out.mkdir(exist_ok=True)
with Scraper(delay_seconds=10) as s:
    for name, url in PAGES.items():
        if s._open(url):
            (out / f"{name}.html").write_text(s.page.content(), encoding="utf-8")
            (out / f"{name}.txt").write_text(s.page.inner_text("body"), encoding="utf-8")
            print("saved", name)
