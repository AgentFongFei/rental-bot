"""Work out which way the front door (and parking entrance) faces.

Three tiers, best first. The source is always reported so the user knows how much to trust it:
  刊登資料   the listing itself says 坐X朝Y / 朝向X
  同社區資料 another listing in the same building says so (cached in data/orientation_cache.json)
  地圖推估   OpenStreetMap: the door is assumed to face the nearest road
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

import requests

_DIR = "東西南北"
_RE_SIT_FACE = re.compile(rf"[坐座]([{_DIR}]{{1,2}})朝([{_DIR}]{{1,2}})")
_RE_FACE = re.compile(rf"(?:朝向|座向|坐向|大門朝|門口朝)\s*[:：]?\s*(?:朝)?([{_DIR}]{{1,2}})")
_OPPOSITE = {"東": "西", "西": "東", "南": "北", "北": "南"}
_CACHE = Path(__file__).resolve().parent.parent / "data" / "orientation_cache.json"
_UA = {"User-Agent": "rental-bot/1.0 (personal weekly search)"}


def _normalise(d: str) -> str:
    # 591 writes both 東南 and 南東; Chinese convention puts 東/西 first.
    if len(d) == 2 and d[0] in "南北" and d[1] in "東西":
        d = d[1] + d[0]
    return d


def _sit_from_face(face: str) -> str:
    return "".join(_OPPOSITE[c] for c in face)


def from_text(text: str) -> dict | None:
    m = _RE_SIT_FACE.search(text)
    if m:
        face = _normalise(m.group(2))
        return {"door": f"坐{_normalise(m.group(1))}朝{face}", "source": "刊登資料"}
    m = _RE_FACE.search(text)
    if m:
        face = _normalise(m.group(1))
        return {"door": f"坐{_sit_from_face(face)}朝{face}", "source": "刊登資料"}
    return None


def _load_cache() -> dict:
    try:
        return json.loads(_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember(community: str, result: dict) -> None:
    """Store a building's orientation so other units in it can reuse it."""
    if not community or result.get("source") != "刊登資料":
        return
    cache = _load_cache()
    cache[community] = {"door": result["door"]}
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def from_community(community: str) -> dict | None:
    hit = _load_cache().get(community) if community else None
    return {"door": hit["door"], "source": "同社區資料"} if hit else None


def _bearing(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def bearing_to_dir(deg: float) -> str:
    names = ["北", "東北", "東", "東南", "南", "西南", "西", "西北"]
    return names[int((deg + 22.5) // 45) % 8]


def from_map(community: str, district: str, county: str) -> dict | None:
    """Geocode the building by name, then face it toward the nearest named road.

    Only attempted when we know the building name: with just a road name the
    point lands mid-road and the answer would be meaningless.
    """
    if not community:
        return None
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{community} {district} {county}", "format": "json", "limit": 1, "countrycodes": "tw"},
            headers=_UA,
            timeout=20,
        )
        time.sleep(1)  # Nominatim allows 1 request/second
        hits = r.json()
        if not hits:
            return None
        lat, lon = float(hits[0]["lat"]), float(hits[0]["lon"])
        q = f"""[out:json][timeout:25];
way(around:80,{lat},{lon})["highway"]["name"]["highway"!~"footway|path|service"];
out geom;"""
        roads = requests.post("https://overpass-api.de/api/interpreter", data={"data": q}, headers=_UA, timeout=40).json()
    except (requests.RequestException, ValueError):
        return None

    nearest: dict[str, tuple[float, float, float]] = {}
    for way in roads.get("elements", []):
        name = way.get("tags", {}).get("name", "")
        for pt in way.get("geometry", []):
            d = math.hypot(pt["lat"] - lat, (pt["lon"] - lon) * math.cos(math.radians(lat)))
            if name not in nearest or d < nearest[name][0]:
                nearest[name] = (d, pt["lat"], pt["lon"])
    if not nearest:
        return None
    ranked = sorted(nearest.items(), key=lambda kv: kv[1][0])
    main_name, (_, rlat, rlon) = ranked[0]
    face = bearing_to_dir(_bearing(lat, lon, rlat, rlon))
    result = {"door": f"坐{_sit_from_face(face)}朝{face}（面向{main_name}）", "source": "地圖推估"}
    if len(ranked) > 1:
        side_name, (_, slat, slon) = ranked[1]
        result["parking"] = f"車道可能在{side_name}側，入口朝{bearing_to_dir(_bearing(lat, lon, slat, slon))}"
    else:
        result["parking"] = f"車道可能也在{main_name}側，入口朝{face}"
    return result


def resolve(listing: dict, county: str) -> dict:
    text = listing.get("text", "")
    community = listing.get("community", "")
    found = from_text(text)
    if found:
        remember(community, found)
        return found
    return (
        from_community(community)
        or from_map(community, listing.get("district", "").split("-")[0], county)
        or {"door": "未標示", "source": "無資料"}
    )
