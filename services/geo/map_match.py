"""Snap a trip's GPS breadcrumbs to the road network (OSRM map matching).

Breadcrumbs are sparse and noisy: a phone in a pocket reports every 30 s at
best, and a fix can be tens of metres off. Joined with straight lines they cut
corners and cross fields. OSRM's /match finds the most likely path along real
roads through the fixes, and routes along roads across the gaps between them,
so an incomplete trail still draws as a drive.

OSRM runs self-hosted (docker-compose service ``osrm``, its map built on
GitHub from osrm/regions.txt), so locations never leave the server. It is optional:
with OSRM_URL unset or the service down, match() returns None and the caller
serves the raw points, marked as not snapped.
"""
from __future__ import annotations

import logging

import aiohttp

log = logging.getLogger("geo.map_match")

#: Breadcrumbs less accurate than this (metres) are left out of the match:
#: a network fix far off the road would pull the path onto the wrong street.
MATCH_MAX_ACCURACY_M = 50.0
#: OSRM's default --max-matching-size; longer trails go in overlapping chunks.
CHUNK = 100


def _radius(acc) -> float:
    """Search radius around a fix for OSRM, from its accuracy (5-50 m)."""
    try:
        a = float(acc)
    except (TypeError, ValueError):
        return 25.0
    return min(MATCH_MAX_ACCURACY_M, max(5.0, a if a > 0 else 25.0))


def usable(points: list[dict]) -> list[dict]:
    """The fixes worth matching: accurate enough, in time order, no repeats."""
    out: list[dict] = []
    for p in sorted(points, key=lambda x: x.get("t", 0)):
        acc = p.get("acc") or 0
        if acc and acc > MATCH_MAX_ACCURACY_M:
            continue
        if out and abs(out[-1]["lat"] - p["lat"]) < 1e-6 and abs(out[-1]["lon"] - p["lon"]) < 1e-6:
            continue
        out.append(p)
    return out


async def _match_chunk(session: aiohttp.ClientSession, base: str, chunk: list[dict]) -> tuple[list, float] | None:
    coords = ";".join(f"{p['lon']:.6f},{p['lat']:.6f}" for p in chunk)
    radiuses = ";".join(f"{_radius(p.get('acc')):.0f}" for p in chunk)
    url = f"{base}/match/v1/driving/{coords}"
    params = {
        "geometries": "geojson",
        "overview": "full",
        # Join across missing stretches along roads rather than splitting the
        # trip wherever the trail has a gap.
        "gaps": "ignore",
        "tidy": "true",
        "radiuses": radiuses,
    }
    async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        data = await resp.json(content_type=None)
    if resp.status != 200 or data.get("code") != "Ok" or not data.get("matchings"):
        log.info("[map_match] OSRM %s: %s", resp.status, data.get("code"))
        return None
    line: list = []
    distance = 0.0
    for m in data["matchings"]:
        line.extend(m.get("geometry", {}).get("coordinates", []))
        distance += float(m.get("distance") or 0.0)
    return line, distance


async def match(points: list[dict], osrm_url: str | None) -> dict | None:
    """The road path through ``points``: {"coordinates": [[lat, lon], ...],
    "distance_m": float, "used": n}, or None when it cannot be matched."""
    base = (osrm_url or "").rstrip("/")
    fixes = usable(points)
    if not base or len(fixes) < 2:
        return None
    line: list = []
    distance = 0.0
    try:
        async with aiohttp.ClientSession() as session:
            # Consecutive chunks share their boundary fix so the path is continuous.
            start = 0
            while start < len(fixes) - 1:
                chunk = fixes[start:start + CHUNK]
                got = await _match_chunk(session, base, chunk)
                if got is None:
                    return None
                part, dist = got
                line.extend(part if not line else part[1:])
                distance += dist
                start += CHUNK - 1
    except Exception as e:  # unreachable, timeout, bad JSON: serve raw points instead
        log.warning("[map_match] OSRM unavailable: %s", e)
        return None
    if len(line) < 2:
        return None
    return {"coordinates": [[lat, lon] for lon, lat in line], "distance_m": distance, "used": len(fixes)}
