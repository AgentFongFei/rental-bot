from pathlib import Path

from rentbot import filters, orientation, telegram
from rentbot.scraper import list_url, parse_detail, parse_list_html

CFG = {
    "max_rent": 25000,
    "count_extra_fees": True,
    "require": {"elevator": True, "cooking": True, "flat_parking": True, "bed": True,
                "basic_furniture": ["衣櫃", "桌椅"]},
    "prefer": {"gas_stove": True},
}
FIXTURES = Path(__file__).parent / "fixtures"
ALL_FACILITIES = ["冰箱", "洗衣機", "冷氣", "床", "衣櫃", "桌椅", "沙發", "電梯", "平面車位"]


def page(name):
    return parse_detail((FIXTURES / f"{name}.html").read_text(), (FIXTURES / f"{name}.txt").read_text())


def fake_html(missing=(), fields=None):
    dls = "".join(
        f'<dl class="{"del" if f in missing else ""}"><dd class="text">{f}</dd></dl>' for f in ALL_FACILITIES
    )
    rows = "".join(
        f'<div class="item"><span class="label">{k}</span><span class="value">{v}</span></div>'
        for k, v in (fields or {"車位": "平面式"}).items()
    )
    return f'<div class="facility">{dls}</div><section class="detail-section">{rows}</section>'


def listing(missing=(), fields=None, text="開伙\n可開伙", **kw):
    base = {"id": "1", "title": "全新兩房", "price": 22000, "district": "竹南鎮-大埔街",
            "tags": ["有電梯", "可開伙"]}
    base.update(parse_detail(fake_html(missing, fields), text))
    base.update(kw)
    return base


def test_passing_listing():
    assert filters.check(listing(), CFG) == []


def test_mechanical_parking_rejected():
    assert "機械車位" in filters.check(listing(fields={"車位": "機械式"}), CFG)


def test_no_parking_rejected():
    assert "沒有平面車位" in filters.check(listing(missing=["平面車位"], fields={"車位": "無"}), CFG)


def test_filter_menu_text_does_not_count_as_parking():
    # The page's search menu says 平面車位; only the listing's own fields count.
    x = listing(missing=["平面車位"], fields={"電梯": "有"}, text="篩選 平面車位 機械車位\n可開伙")
    assert "沒有平面車位" in filters.check(x, CFG)


def test_empty_unit_rejected():
    assert "空屋無家具" in filters.check(listing(title="２房空屋，可租補"), CFG)


def test_other_ads_on_page_saying_empty_do_not_reject():
    assert filters.check(listing(text="可開伙\n推薦物件\n全新2房空屋出租"), CFG) == []


def test_greyed_out_bed_rejected():
    assert "沒有床" in filters.check(listing(missing=["床"]), CFG)


def test_management_and_parking_fees_count_toward_budget():
    x = listing(price=19800, fields={"車位": "平面式", "管理費": "3,450元/月", "車位租金": "2,000元/月"})
    assert x["extra_fees"] == 5450
    assert any("超過上限" in r for r in filters.check(x, CFG))


def test_fee_none_or_included_is_zero():
    x = listing(fields={"車位": "平面式", "管理費": "無", "車位租金": "已含"})
    assert x["extra_fees"] == 0 and not x["parking_fee_unknown"]


# Real 591 pages saved on 2026-10-03 (trimmed to the parts we read).

def test_real_page_flat_parking_with_orientation():
    d = page("detail_flat_parking_orientation")
    assert d["parking"] == "平面"
    assert d["facing"] == "坐西朝東"
    assert d["community"] == "富比市"
    assert d["address"] == "竹南鎮大埔街"
    assert {"床", "衣櫃", "桌椅"} <= d["facilities"]
    assert "天然瓦斯" not in d["facilities"]  # greyed out on this listing
    assert d["parking_fee_unknown"]


def test_real_page_mechanical_parking():
    assert page("detail_mechanical")["parking"] == "機械"


def test_real_page_missing_wardrobe_and_table():
    d = page("detail_empty_unit")
    assert "衣櫃" not in d["facilities"] and "桌椅" not in d["facilities"]
    assert d["extra_fees"] == 1500


def test_districts():
    assert filters.in_districts({"district": "頭份市-公北三路"}, ["竹南鎮", "頭份市"])
    assert not filters.in_districts({"district": "後龍鎮-校椅二路"}, ["竹南鎮", "頭份市"])
    assert not filters.in_districts({"district": "香山區-中華路"}, ["東區", "北區"])


def test_orientation_from_listing_text():
    assert orientation.from_text("屋況佳 坐西朝東 採光好") == {"door": "坐西朝東", "source": "刊登資料"}
    assert orientation.from_text("朝向：南東")["door"] == "坐西北朝東南"
    assert orientation.from_text("面向公園") is None


def test_bearing_names():
    assert orientation.bearing_to_dir(0) == "北"
    assert orientation.bearing_to_dir(135) == "東南"
    assert orientation.bearing_to_dir(350) == "北"


def test_ranking_prefers_gas_stove_then_known_orientation():
    a = listing(id="a", orientation={"source": "無資料"})
    b = listing(id="b", text="可開伙\n附瓦斯爐", orientation={"source": "無資料"})
    c = listing(id="c", orientation={"source": "刊登資料"})
    ranked = sorted([a, b, c], key=lambda x: filters.score(x, CFG))
    assert [x["id"] for x in ranked] == ["b", "c", "a"]


def test_orientation_from_facing_field():
    assert orientation.resolve({"facing": "坐西朝東"}, "苗栗縣")["door"] == "坐西朝東"
    assert orientation.resolve({"facing": "朝南"}, "苗栗縣")["door"] == "坐北朝南"


def test_list_url():
    assert list_url(7, 1, 25000, 2) == (
        "https://rent.591.com.tw/list?region=7&kind=1&price=0$_25000$&other=lift,cook&option=bed&layout=2")


def test_parse_list_html():
    html = """
    <div class="item" data-id="21990533">
      <a class="link v-middle" href="https://rent.591.com.tw/21990533">稀有雙主臥套房</a>
      <span class="tag">電梯</span><span class="tag">可開伙</span>
      <span class="line">2房1廳</span><span class="line">20坪</span><span class="line">9F/9F</span>
      <span>竹南鎮-大埔街</span>
      <strong class="font-arial">22,000</strong>
    </div>
    <a href="?page=1">1</a><a href="?page=3">3</a>"""
    items, pages = parse_list_html(html)
    assert pages == 3
    x = items[0]
    assert (x["id"], x["price"], x["district"], x["area_ping"], x["floor"], x["layout"]) == (
        "21990533", 22000, "竹南鎮-大埔街", "20", "9F/9F", "2房1廳")


def test_telegram_message_splits_and_escapes():
    xs = [listing(id=str(i), title=f"A&B {i}", link="https://rent.591.com.tw/1",
                  layout="2房", area_ping="20", floor="3F/5F",
                  orientation={"door": "坐西朝東", "source": "刊登資料"}) for i in range(20)]
    msgs = telegram.build_messages("header", xs)
    assert all(len(m) <= 4096 for m in msgs)
    assert "A&amp;B 0" in msgs[0]
    assert sum(m.count("591 物件頁") for m in msgs) == 20
