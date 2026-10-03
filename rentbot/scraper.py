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


def _fee(value: str) -> int:
    """Monthly amount in a price field; 0 for 無 / 含 / 已含 / unknown."""
    m = re.search(r"([\d,]{3,7})\s*元", value)
    return int(m.group(1).replace(",", "")) if m else 0


def parse_detail(html: str, text: str) -> dict:
    """Read a rendered 591 detail page.

    Layout as of 2026-10: facilities are `.facility dl` (class "del" = not
    provided); the 房屋詳情/房屋價格 sections are `.item` rows of
    `span.label` + `span.value`.
    """
    from rentbot.filters import parking_type

    soup = BeautifulSoup(html, "html.parser")
    fields: dict[str, str] = {}
    for item in soup.select(".item"):
        label, value = item.select_one("span.label"), item.select_one("span.value")
        if label and value:
            fields.setdefault(label.get_text(strip=True), value.get_text(" ", strip=True))

    facilities: set[str] = set()
    facility_parking = ""
    for dl in soup.select(".facility dl"):
        if "del" in (dl.get("class") or []):
            continue
        name = dl.get_text(strip=True)
        if name.endswith("車位"):
            facility_parking = name
            facilities.add("車位")
        else:
            facilities.add(re.sub(r"^\d+", "", name))  # "2陽台" -> "陽台"

    address_el = soup.select_one('[data-gtm-behavior="address"]')
    community_el = soup.select_one('a[href*="market.591.com.tw"]')

    parking_fee = fields.get("車位租金", "")
    return {
        "text": text,
        "fields": fields,
        "facilities": facilities,
        "address": address_el.get_text(strip=True) if address_el else "",
        "community": community_el.get_text(strip=True) if community_el else "",
        "community_url": community_el.get("href", "") if community_el else "",
        "parking": parking_type(fields.get("車位", "")) or parking_type(facility_parking),
        "extra_fees": _fee(fields.get("管理費", "")) + _fee(parking_fee),
        "parking_fee_unknown": "另計" in parking_fee and not _fee(parking_fee),
        "facing": fields.get("朝向", ""),
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
            if not self._wait("div.item[data-id]"):
                # Sometimes the list renders late; one slow retry before giving up.
                self.page.reload(wait_until="domcontentloaded")
                if not self._wait("div.item[data-id]", 30000):
                    print(f"[scraper] 列表沒有物件或版面改了：{self.page.url}")
            items, total = parse_list_html(self.page.content())
            out.extend(items)
            if not items or n >= total:
                break
        return out

    def _wait(self, selector: str, timeout: int = 15000) -> bool:
        try:
            self.page.wait_for_selector(selector, timeout=timeout)
            return True
        except Exception:  # noqa: BLE001
            return False

    def detail(self, listing: dict) -> dict | None:
        """Parsed detail page, or None when it never finished rendering."""
        if not self._open(listing["link"]):
            return None
        if not self._wait(".facility dl"):
            self.page.reload(wait_until="domcontentloaded")
        if not self._wait(".facility dl") or not self._wait("span.label"):
            print(f"[scraper] 物件頁沒載入完成，略過：{listing['link']}")
            return None
        return parse_detail(self.page.content(), self.page.inner_text("body"))

    def dump(self, url: str, path: str) -> None:
        """Save a rendered page, for fixing selectors when 591 changes its layout."""
        if self._open(url):
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.page.content())
