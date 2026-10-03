"""Which way a building's main gate and car-park driveway face, looked up per community.

Owner-stated 坐向 is not used (owners and agents describe it inconsistently).
Instead, for each 591 community name:

  1. Text research: Claude searches property sites (樂居, 樂屋網, 591 社區, 預售屋 blogs...)
     for which road the gate and the driveway are on.
  2. Street View: photos are taken from each nearby road looking at the building,
     and Claude picks the photo showing the gate / the driveway ramp. The camera
     heading gives the direction the gate faces.
  3. Text and Street View agreeing on the road -> 確定; only one of them -> 推估.

Results are cached in data/communities.json, so each community is researched once.
Needs ANTHROPIC_API_KEY; Street View additionally needs GOOGLE_MAPS_API_KEY.
"""

from __future__ import annotations

import base64
import json
import math
import os
import time
from datetime import date
from pathlib import Path

import requests

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "communities.json"
RETRY_EMPTY_AFTER_DAYS = 30
MODEL = "claude-opus-5-5"
_UA = {"User-Agent": "rental-bot/1.0 (personal weekly search)"}
_DIRS = ["北", "東北", "東", "東南", "南", "西南", "西", "西北"]


# --- geometry -----------------------------------------------------------------

def bearing(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def bearing_to_dir(deg: float) -> str:
    return _DIRS[int((deg % 360 + 22.5) // 45) % 8]


def metres(lat1, lon1, lat2, lon2) -> float:
    return math.hypot((lat2 - lat1) * 111_320, (lon2 - lon1) * 111_320 * math.cos(math.radians(lat1)))


def facing_from_camera(camera_heading: float) -> str:
    """A gate seen by a camera looking along `camera_heading` faces back toward the camera."""
    return bearing_to_dir(camera_heading + 180)


# --- locating the building and its roads -------------------------------------

def geocode(query: str, google_key: str | None) -> tuple[float, float] | None:
    try:
        if google_key:
            r = requests.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": query, "key": google_key, "language": "zh-TW", "region": "tw"},
                timeout=20,
            ).json()
            if r.get("results"):
                loc = r["results"][0]["geometry"]["location"]
                return loc["lat"], loc["lng"]
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "tw"},
            headers=_UA,
            timeout=20,
        ).json()
        time.sleep(1)  # Nominatim allows 1 request/second
        if r:
            return float(r[0]["lat"]), float(r[0]["lon"])
    except (requests.RequestException, ValueError, KeyError):
        pass
    return None


def nearby_roads(lat: float, lon: float, radius: int = 90) -> dict[str, list[tuple[float, float]]]:
    """Named roads around the building, as {name: [(lat, lon), ...]} from OpenStreetMap."""
    q = f"""[out:json][timeout:25];
way(around:{radius},{lat},{lon})["highway"]["name"]["highway"!~"footway|path|cycleway|steps"];
out geom;"""
    try:
        data = requests.post(
            "https://overpass-api.de/api/interpreter", data={"data": q}, headers=_UA, timeout=40
        ).json()
    except (requests.RequestException, ValueError):
        return {}
    roads: dict[str, list[tuple[float, float]]] = {}
    for way in data.get("elements", []):
        pts = [(p["lat"], p["lon"]) for p in way.get("geometry", [])]
        roads.setdefault(way["tags"]["name"], []).extend(pts)
    return roads


def side_of(lat, lon, road_points) -> str:
    """Direction from the building to the nearest point of a road."""
    plat, plon = min(road_points, key=lambda p: metres(lat, lon, *p))
    return bearing_to_dir(bearing(lat, lon, plat, plon))


# --- Street View ---------------------------------------------------------------

def street_view_shots(lat, lon, roads, google_key, per_road=3, max_shots=12) -> list[dict]:
    """Photos from points along each nearby road, each camera aimed at the building."""
    shots: list[dict] = []
    seen_panos: set[str] = set()
    for name, pts in sorted(roads.items(), key=lambda kv: min(metres(lat, lon, *p) for p in kv[1])):
        chosen: list[tuple[float, float]] = []
        for p in sorted(pts, key=lambda p: metres(lat, lon, *p)):
            if metres(lat, lon, *p) > 120:
                break
            if all(metres(*p, *c) > 25 for c in chosen):
                chosen.append(p)
            if len(chosen) == per_road:
                break
        for plat, plon in chosen:
            if len(shots) >= max_shots:
                return shots
            try:
                meta = requests.get(
                    "https://maps.googleapis.com/maps/api/streetview/metadata",
                    params={"location": f"{plat},{plon}", "source": "outdoor", "radius": 30, "key": google_key},
                    timeout=20,
                ).json()
                if meta.get("status") != "OK" or meta.get("pano_id") in seen_panos:
                    continue
                seen_panos.add(meta["pano_id"])
                clat, clon = meta["location"]["lat"], meta["location"]["lng"]
                heading = bearing(clat, clon, lat, lon)
                img = requests.get(
                    "https://maps.googleapis.com/maps/api/streetview",
                    params={"size": "640x400", "pano": meta["pano_id"], "heading": f"{heading:.0f}",
                            "fov": 90, "key": google_key},
                    timeout=30,
                )
            except (requests.RequestException, ValueError, KeyError):
                continue
            if img.ok and img.headers.get("content-type", "").startswith("image/"):
                shots.append({"road": name, "heading": heading, "jpeg": img.content})
    return shots


# --- Claude --------------------------------------------------------------------

_TEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "gate_road": {"type": "string", "description": "大門/大廳主出入口所在的路名，查不到填空字串"},
        "driveway_road": {"type": "string", "description": "停車場車道出入口所在的路名，查不到填空字串"},
        "evidence": {"type": "string", "description": "原文摘錄，說明依據"},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["gate_road", "driveway_road", "evidence", "sources"],
    "additionalProperties": False,
}

_VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "gate_photo": {"type": "integer", "description": "看得到社區大門/大廳入口的照片編號，沒有填 -1"},
        "gate_confidence": {"type": "string", "enum": ["high", "low", "none"]},
        "driveway_photo": {"type": "integer", "description": "看得到地下室車道斜坡入口的照片編號，沒有填 -1"},
        "driveway_confidence": {"type": "string", "enum": ["high", "low", "none"]},
        "notes": {"type": "string"},
    },
    "required": ["gate_photo", "gate_confidence", "driveway_photo", "driveway_confidence", "notes"],
    "additionalProperties": False,
}


def _ask(client, content: list, schema: dict, tools: list | None = None) -> dict | None:
    import anthropic

    messages = [{"role": "user", "content": content}]
    kwargs = {"tools": tools} if tools else {}
    for _ in range(4):  # server tools may pause a long turn; resend to continue
        try:
            response = client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
                messages=messages,
                **kwargs,
            )
        except anthropic.APIStatusError as e:
            print(f"[orientation] Claude API 錯誤 {e.status_code}: {e.message}")
            return None
        except anthropic.APIConnectionError:
            print("[orientation] 連不上 Claude API")
            return None
        if response.stop_reason == "pause_turn":
            messages = [messages[0], {"role": "assistant", "content": response.content}]
            continue
        if response.stop_reason == "refusal":
            return None
        text = next((b.text for b in reversed(response.content) if b.type == "text"), "")
        try:
            return json.loads(text)
        except ValueError:
            return None
    return None


def research_text(client, community: str, town: str, roads: list[str]) -> dict | None:
    prompt = (
        f"請上網查詢苗栗縣{town}的社區「{community}」：\n"
        "1. 社區大門（大廳主出入口）在哪一條路上？\n"
        "2. 地下停車場的車道出入口在哪一條路上？\n"
        "可參考樂居、樂屋網、591 社區/新建案、新竹房地王、預售屋部落格、建商網站等。"
        f"社區附近的道路有：{'、'.join(roads) or '不明'}。\n"
        "只採用資料中明確寫出的內容，例如「車道出入口位於民族路394巷」。"
        "只有基地位置（例如某路口交叉口）而沒寫明大門或車道在哪，就填空字串，不要推測。"
    )
    return _ask(
        client,
        [{"type": "text", "text": prompt}],
        _TEXT_SCHEMA,
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}],
    )


def read_street_view(client, community: str, shots: list[dict]) -> dict | None:
    content: list = []
    for i, s in enumerate(shots):
        content.append({"type": "text", "text": f"照片 {i}：站在{s['road']}上，面向社區拍攝"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg",
                       "data": base64.standard_b64encode(s["jpeg"]).decode()},
        })
    content.append({"type": "text", "text": (
        f"以上是社區「{community}」周圍的 Google 街景。請找出：\n"
        "1. 哪一張照得到社區的大門或大廳主出入口（有門廳、警衛室、社區名稱招牌的入口）。\n"
        "2. 哪一張照得到地下停車場的車道斜坡入口（往下的車道、柵欄、「出入口」標示）。\n"
        "看不清楚或可能是隔壁建築時，confidence 填 low；完全沒有就填 -1 和 none。"
    )})
    return _ask(client, content, _VISION_SCHEMA)


# --- combining -------------------------------------------------------------------

def _same_road(a: str, b: str) -> bool:
    return bool(a and b) and (a in b or b in a)


def combine(photo_index, confidence, text_road, shots, lat, lon, roads) -> dict | None:
    photo = shots[photo_index] if 0 <= photo_index < len(shots) else None
    if photo and confidence != "none":
        agree = _same_road(photo["road"], text_road)
        return {
            "dir": facing_from_camera(photo["heading"]),
            "road": photo["road"],
            "level": "確定" if agree else "推估",
            "how": "街景＋文字資料" if agree else "街景",
        }
    if text_road:
        match = next((pts for name, pts in roads.items() if _same_road(name, text_road)), None)
        return {
            "dir": side_of(lat, lon, match) if match else "",
            "road": text_road,
            "level": "推估",
            "how": "文字資料",
        }
    return None


def research(community: str, town: str, photo_dir: Path | None = None) -> dict:
    """Look up one community's gate and driveway. `photo_dir` saves the Street View shots."""
    import anthropic

    today = str(date.today())
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"checked": today, "gate": None, "driveway": None, "note": "未設定 ANTHROPIC_API_KEY"}
    google_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    client = anthropic.Anthropic()

    loc = geocode(f"苗栗縣{town} {community}", google_key)
    roads = nearby_roads(*loc) if loc else {}
    text = research_text(client, community, town, list(roads)) or {}
    shots = street_view_shots(*loc, roads, google_key) if loc and roads and google_key else []
    vision = (read_street_view(client, community, shots) if shots else None) or {}
    if photo_dir:
        photo_dir.mkdir(parents=True, exist_ok=True)
        for i, s in enumerate(shots):
            (photo_dir / f"{i:02d}_{s['road']}_{s['heading']:.0f}.jpg").write_bytes(s["jpeg"])
        (photo_dir / "vision.json").write_text(json.dumps(vision, ensure_ascii=False, indent=2), encoding="utf-8")

    lat, lon = loc or (0.0, 0.0)
    return {
        "checked": today,
        "location": [lat, lon] if loc else None,
        "gate": combine(vision.get("gate_photo", -1), vision.get("gate_confidence", "none"),
                        text.get("gate_road", ""), shots, lat, lon, roads),
        "driveway": combine(vision.get("driveway_photo", -1), vision.get("driveway_confidence", "none"),
                            text.get("driveway_road", ""), shots, lat, lon, roads),
        "sources": text.get("sources", []),
        "evidence": text.get("evidence", ""),
        "street_view_photos": len(shots),
        "note": "" if google_key else "未設定 GOOGLE_MAPS_API_KEY，只用文字資料",
    }


# --- cache -----------------------------------------------------------------------

def load_cache() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def needs_research(entry: dict | None) -> bool:
    if entry is None:
        return True
    if "未設定" in entry.get("note", "") and (
        os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("GOOGLE_MAPS_API_KEY")
    ):
        return True  # keys were added since the last look
    if entry.get("gate") and entry.get("driveway"):
        return False
    try:
        age = (date.today() - date.fromisoformat(entry["checked"])).days
    except (KeyError, ValueError):
        return True
    return age >= RETRY_EMPTY_AFTER_DAYS


def lookup(community: str, town: str, cache: dict) -> dict | None:
    """Cached orientation for a community, researching it first if needed."""
    if not community:
        return None
    key = f"{town}/{community}"
    if needs_research(cache.get(key)):
        print(f"[orientation] 查詢社區：{key}")
        cache[key] = research(community, town)
    return cache[key]


def describe(part: dict | None) -> str:
    if not part:
        return "查無資料"
    where = f"（{part['road']}側）" if part.get("road") else ""
    facing = f"朝{part['dir']}" if part.get("dir") else "方位不明"
    return f"{facing}{where}｜{part['level']}，依{part['how']}"
