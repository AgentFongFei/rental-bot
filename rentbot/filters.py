"""Hard filters and ranking. Pure functions, no network, so they are easy to test."""

from __future__ import annotations

import re

_RE_PARKING_FLAT = re.compile(r"坡道平面|平面式|平面車位|平車")
_RE_PARKING_MECH = re.compile(r"機械式|機械車位|升降")
_RE_GAS_STOVE = re.compile(r"瓦斯爐|爐台|爐具")
_RE_FEE = re.compile(r"(管理費|車位費|車位租金)\D{0,6}?([\d,]{3,6})\s*元")
_RE_EMPTY_UNIT = re.compile(r"空屋|無家具|不含家具")


def in_districts(listing: dict, districts: list[str]) -> bool:
    district = listing.get("district", "")
    return any(district.startswith(d) for d in districts)


def parking_type(text: str) -> str | None:
    """Return '平面', '機械', or None when the text doesn't say."""
    if _RE_PARKING_MECH.search(text):
        return "機械"
    if _RE_PARKING_FLAT.search(text):
        return "平面"
    return None


def extra_fees(text: str) -> int:
    """Sum monthly fees that are charged on top of rent (管理費, 另計的車位費)."""
    total = 0
    for m in _RE_FEE.finditer(text):
        context = text[max(0, m.start() - 8) : m.end() + 8]
        if "含" in context and "另" not in context:
            continue  # e.g. 租金含管理費
        total += int(m.group(2).replace(",", ""))
    return total


def has_gas_stove(listing: dict) -> bool:
    if _RE_GAS_STOVE.search(listing.get("text", "")):
        return True
    return "瓦斯爐" in listing.get("facilities", set())


def check(listing: dict, cfg: dict) -> list[str]:
    """Return the reasons a listing fails; an empty list means it passes."""
    req = cfg["require"]
    facilities: set[str] = listing.get("facilities", set())
    text = listing.get("text", "")
    reasons: list[str] = []

    rent = listing.get("price", 0)
    total = rent + (listing.get("extra_fees", 0) if cfg.get("count_extra_fees") else 0)
    if rent > cfg["max_rent"]:
        reasons.append(f"租金 {rent} 超過上限")
    elif total > cfg["max_rent"]:
        reasons.append(f"加管理費/車位費後 {total} 超過上限")

    if req.get("elevator") and "電梯" not in facilities and "電梯" not in listing.get("tags", []):
        reasons.append("沒有電梯")
    if req.get("cooking") and "可開伙" not in text and "可開伙" not in listing.get("tags", []):
        reasons.append("不確定可開伙")
    if req.get("flat_parking"):
        kind = listing.get("parking")
        if kind == "機械":
            reasons.append("機械車位")
        elif kind != "平面":
            reasons.append("沒有平面車位")
    if _RE_EMPTY_UNIT.search(listing.get("title", "") + text):
        reasons.append("空屋無家具")
    if req.get("bed") and "床" not in facilities:
        reasons.append("沒有床")
    for item in req.get("basic_furniture", []):
        if item not in facilities:
            reasons.append(f"沒有{item}")
    return reasons


def score(listing: dict, cfg: dict) -> tuple:
    """Sort key: lower is better."""
    orientation = listing.get("orientation") or {}
    return (
        0 if cfg.get("prefer", {}).get("gas_stove") and has_gas_stove(listing) else 1,
        {"刊登資料": 0, "同社區資料": 1, "地圖推估": 2}.get(orientation.get("source"), 3),
        listing.get("price", 0) + listing.get("extra_fees", 0),
        -float(listing.get("area_ping") or 0),
    )
