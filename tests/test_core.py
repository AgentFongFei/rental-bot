from rentbot import filters, orientation, telegram
from rentbot.scraper import list_url, parse_detail, parse_list_html

CFG = {
    "max_rent": 25000,
    "count_extra_fees": True,
    "require": {"elevator": True, "cooking": True, "flat_parking": True, "bed": True,
                "basic_furniture": ["衣櫃", "桌椅"]},
    "prefer": {"gas_stove": True},
}
FULL = {"冰箱": True, "洗衣機": True, "冷氣": True, "床": True, "衣櫃": True,
        "桌椅": True, "天然瓦斯": True, "沙發": True, "電梯": True}


def listing(**kw):
    base = {"id": "1", "title": "全新兩房", "price": 22000, "district": "竹南鎮-大埔街",
            "tags": ["電梯", "可開伙"]}
    base.update(parse_detail(kw.pop("text", "車位\n平面式\n可開伙"), kw.pop("fac", FULL)))
    base.update(kw)
    return base


def test_passing_listing():
    assert filters.check(listing(), CFG) == []


def test_mechanical_parking_rejected():
    assert "機械車位" in filters.check(listing(text="車位\n機械式\n可開伙"), CFG)


def test_no_parking_info_rejected():
    assert "沒有平面車位" in filters.check(listing(text="可開伙"), CFG)


def test_empty_unit_rejected_even_if_icons_present():
    assert "空屋無家具" in filters.check(listing(title="２房空屋，可租補"), CFG)


def test_greyed_out_bed_rejected():
    assert "沒有床" in filters.check(listing(fac={**FULL, "床": False}), CFG)


def test_extra_fees_push_over_budget():
    x = listing(price=19800, text="車位\n平面式\n可開伙\n管理費3,450元/月\n車位費2,000元/月")
    assert x["extra_fees"] == 5450
    assert any("超過上限" in r for r in filters.check(x, CFG))


def test_fee_included_in_rent_not_counted():
    assert filters.extra_fees("租金含管理費1,500元") == 0


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
    a = listing(id="a", text="車位\n平面式\n可開伙", orientation={"source": "無資料"})
    b = listing(id="b", text="車位\n平面式\n可開伙\n附瓦斯爐", orientation={"source": "無資料"})
    c = listing(id="c", text="車位\n平面式\n可開伙", orientation={"source": "刊登資料"})
    ranked = sorted([a, b, c], key=lambda x: filters.score(x, CFG))
    assert [x["id"] for x in ranked] == ["b", "c", "a"]


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
