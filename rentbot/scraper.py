"""591 scraper using a real (headless) browser.

591's JSON API needs a CSRF token and returns encrypted payloads, so instead we
let Chromium render the pages and read the DOM, which is what a person sees.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from playwright.sync_api import Page, sync_playwright

BASE_URL = "https://rent.591.com.tw"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

_RE_PAGE = re.compile(r"page=(\d+)")
_RE_PING = re.compile(r"([\d.]+)\s*坪")
_RE_DISTRICT = re.compile(r"^[^\s]+?[鎮市區鄉][-—]")

FACILITY_NAMES = [
    "冰箱", "洗衣機", "電視", "冷氣", "熱水器", "床", "衣櫃", "第四台",
    "網路", "天然瓦斯", "瓦斯爐", "沙發", "桌椅", "陽台", "電梯", "車位",
]

# Runs inside the detail page. 591 shows every facility icon and greys out the
# ones that aren't provided, so we keep only the items that don't look disabled.
_FACILITY_JS = """
(names) => {
  const looksOff = (el) => {
    for (let n = el, i = 0; n && i < 4; n = n.parentElement, i++) {
      const cls = (n.className && n.className.baseVal !== undefined) ? n.className.baseVal : (n.className || '');
      if (/(^|[\\s_-])(del|disabled?|no|none|gray|grey|inactive|lack)([\\s_-]|$)/i.test(cls)) return true;
      const st = getComputedStyle(n);
      if (parseFloat(st.opacity) < 0.6 || st.textDecorationLine.includes('line-through')) return true;
    }
    return false;
  };
  const found = {};
  for (const el of document.querySelectorAll('body *')) {
    if (el.children.length) continue;
    const t = (el.textContent || '').trim();
    if (!names.includes(t)) continue;
    const off = looksOff(el);
    found[t] = (found[t] === true) || !off;
  }
  return found;
}
"""


def list_url(region: int, kind: int, max_rent: int, rooms: int | None) -> str:
    url = f"{BASE_URL}/list?region={region}&kind={kind}&price=0$_{max_rent}$&other=lift,cook&option=bed"
    if rooms:
        url += f"&layout={rooms}"
    return url


def _int(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 0


def parse_list_html(html: str) -> tuple[list[dict], int]:
    """Return (listings on this page, total page count)."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for item in soup.find_all("div", class_="item", attrs={"data-id": True}):
        link_el = item.select_one("a.link.v-middle") or item.select_one("a[href]")
        href = link_el.get("href", "") if link_el else ""
        price_el = item.select_one("strong.text-26px, strong.font-arial")
        spans = [s.get_text(strip=True) for s in item.find_all("span") if s.get_text(strip=True)]
        lines = [s.get_text(strip=True) for s in item.select("span.line")]
        ping = next((m.group(1) for s in lines if (m := _RE_PING.search(s))), "")
        results.append({
            "id": item["data-id"],
            "title": link_el.get_text(strip=True) if link_el else "",
            "link": href if href.startswith("http") else f"{BASE_URL}/{item['data-id']}",
            "price": _int(price_el.get_text()) if price_el else 0,
            "tags": [t.get_text(strip=True) for t in item.select("span.tag")],
            "district": next((s for s in spans if _RE_DISTRICT.search(s)), ""),
            "layout": next((s for s in lines if "房" in s), ""),
            "area_ping": ping,
            "floor": next((s for s in lines if "F" in s), ""),
        })
    nums = [int(m.group(1)) for a in soup.select('a[href*="page="]') if (m := _RE_PAGE.search(a.get("href", "")))]
    return results, max(nums) if nums else 1


def _labelled(text: str, label: str) -> str:
    """Value that follows a label on its own line, e.g. '車位\\n平面式'."""
    m = re.search(rf"{label}\s*[:：]?\s*\n?\s*([^\n]{{1,40}})", text)
    return m.group(1).strip() if m else ""


def parse_detail(text: str, facilities: dict[str, bool]) -> dict:
    from rentbot.filters import extra_fees, parking_type

    community = _labelled(text, "社區")
    address = _labelled(text, "地址")
    parking_line = _labelled(text, "車位")
    return {
        "text": text,
        "facilities": {name for name, on in facilities.items() if on},
        "community": "" if community in ("", "無", "-") else community,
        "address": address,
        "parking": parking_type(parking_line) or parking_type(text),
        "extra_fees": extra_fees(text),
    }


class Scraper:
    def __init__(self, delay_seconds: float = 3, headless: bool = True):
        self.delay_ms = int(delay_seconds * 1000)
        self.headless = headless

    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=self.headless, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = self._browser.new_context(user_agent=USER_AGENT, locale="zh-TW", timezone_id="Asia/Taipei")
        self.page: Page = ctx.new_page()
        return self

    def __exit__(self, *exc):
        self._browser.close()
        self._pw.stop()

    def _open(self, url: str) -> bool:
        # 591 keeps ads and trackers polling, so "networkidle" never fires and
        # every page would hit the timeout. Wait for the DOM, then a fixed pause.
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:  # noqa: BLE001
            print(f"[scraper] 載入失敗 {url}: {e}")
            return False
        self.page.wait_for_timeout(self.delay_ms)
        return True

    def search(self, url: str, max_pages: int) -> list[dict]:
        out: list[dict] = []
        for n in range(1, max_pages + 1):
            if not self._open(url if n == 1 else f"{url}&page={n}"):
                break
            try:
                self.page.wait_for_selector("div.item[data-id]", timeout=15000)
            except Exception:  # noqa: BLE001
                print(f"[scraper] 列表沒有物件或版面改了：{self.page.url}")
            items, total = parse_list_html(self.page.content())
            out.extend(items)
            if not items or n >= total:
                break
        return out

    def detail(self, listing: dict) -> dict | None:
        if not self._open(listing["link"]):
            return None
        text = self.page.inner_text("body")
        facilities = self.page.evaluate(_FACILITY_JS, FACILITY_NAMES)
        return parse_detail(text, facilities)

    def dump(self, url: str, path: str) -> None:
        """Save a rendered page, for fixing selectors when 591 changes its layout."""
        if self._open(url):
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.page.content())
