"""Which map regions trips need that the road network (OSRM) does not have.

OSRM serves the regions listed in osrm/regions.txt. A trip that starts or ends
outside them cannot be snapped to roads. Rather than keep a second copy of
that list here, OSRM is asked directly: its /nearest road for a point outside
the map is kilometres away. The region to add is then the smallest Geofabrik
extract containing the point, found in Geofabrik's public index (region
outlines with their download URLs). Needed regions are kept in Redis
(``osrm:regions:needed``) for the map to be extended with.
"""
from __future__ import annotations

import json
import logging
import time

import aiohttp

log = logging.getLogger("geo.map_regions")

GEOFABRIK_INDEX_URL = "https://download.geofabrik.de/index-v1.json"
INDEX_KEY = "geo:geofabrik_index"
INDEX_TTL_S = 7 * 86400
NEEDED_KEY = "osrm:regions:needed"
#: A road further than this from a point means the point is off the map.
OFF_MAP_M = 2000.0


async def off_map(lat: float, lon: float, osrm_url: str | None) -> bool | None:
    """True when OSRM's map does not cover the point; None when it cannot say."""
    base = (osrm_url or "").rstrip("/")
    if not base:
        return None
    try:
        async with aiohttp.ClientSession() as s, s.get(
            f"{base}/nearest/v1/driving/{lon:.6f},{lat:.6f}", params={"number": "1"},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            data = await resp.json(content_type=None)
    except Exception as e:
        log.info("[map_regions] OSRM unavailable: %s", e)
        return None
    if data.get("code") != "Ok" or not data.get("waypoints"):
        return True
    return float(data["waypoints"][0].get("distance") or 0.0) > OFF_MAP_M


def _in_ring(lat: float, lon: float, ring: list) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _contains(geometry: dict, lat: float, lon: float) -> bool:
    polys = geometry.get("coordinates") or []
    if geometry.get("type") == "Polygon":
        polys = [polys]
    for poly in polys:
        if poly and _in_ring(lat, lon, poly[0]) and not any(_in_ring(lat, lon, hole) for hole in poly[1:]):
            return True
    return False


def region_for(index: dict, lat: float, lon: float) -> dict | None:
    """The smallest region (one with no subregions) containing the point:
    {"id", "name", "url"} or None."""
    features = index.get("features") or []
    parents = {f.get("properties", {}).get("parent") for f in features}
    best = None
    for f in features:
        props = f.get("properties") or {}
        url = (props.get("urls") or {}).get("pbf")
        if not url or props.get("id") in parents or not f.get("geometry"):
            continue  # not downloadable, or has subregions (prefer the smallest)
        if _contains(f["geometry"], lat, lon):
            best = {"id": props.get("id"), "name": props.get("name"), "url": url}
            break
    return best


async def _index(r) -> dict | None:
    cached = await r.get(INDEX_KEY) if r else None
    if cached:
        try:
            return json.loads(cached)
        except ValueError:
            pass
    try:
        async with aiohttp.ClientSession() as s, s.get(
            GEOFABRIK_INDEX_URL, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            index = await resp.json(content_type=None)
    except Exception as e:
        log.warning("[map_regions] Geofabrik index unavailable: %s", e)
        return None
    if r:
        await r.set(INDEX_KEY, json.dumps(index), ex=INDEX_TTL_S)
    return index


async def note_if_off_map(r, lat: float, lon: float, osrm_url: str | None) -> dict | None:
    """Record the region for a point the map does not cover; returns it."""
    if not await off_map(lat, lon, osrm_url):
        return None
    index = await _index(r)
    region = region_for(index or {}, lat, lon)
    if not region:
        return None
    if r and await r.hget(NEEDED_KEY, region["url"]) is None:
        await r.hset(NEEDED_KEY, region["url"], json.dumps({**region, "first_seen": time.time()}))
        log.info("[map_regions] Trips need map region %s (%s)", region["name"], region["url"])
    return region


async def needed(r) -> list[dict]:
    """Regions trips have needed that the map lacks, oldest first."""
    raw = await r.hgetall(NEEDED_KEY) if r else {}
    out = []
    for value in raw.values():
        try:
            out.append(json.loads(value))
        except ValueError:
            continue
    return sorted(out, key=lambda x: x.get("first_seen", 0))
