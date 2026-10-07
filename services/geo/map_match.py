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


class _NoMatch(Exception):
    """OSRM answered, and the fixes fit no road."""


async def _match_chunk(session: aiohttp.ClientSession, base: str, chunk: list[dict]) -> tuple[list, float, float]:
    """(line [[lon, lat], ...], distance m, confidence x distance) for one
    chunk. Raises _NoMatch when OSRM finds no road path through the fixes."""
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
    if data.get("code") in ("NoMatch", "NoSegment", "TooBig") or (resp.status == 200 and not data.get("matchings")):
        raise _NoMatch(data.get("code"))
    if resp.status != 200 or data.get("code") != "Ok":
        raise RuntimeError(f"OSRM {resp.status}: {data.get('code')}")
    line: list = []
    distance = 0.0
    weighted = 0.0
    for m in data["matchings"]:
        line.extend(m.get("geometry", {}).get("coordinates", []))
        d = float(m.get("distance") or 0.0)
        distance += d
        weighted += d * float(m.get("confidence") or 0.0)
    return line, distance, weighted


async def match(points: list[dict], osrm_url: str | None) -> dict | None:
    """The road path through ``points``.

    {"matched": True, "coordinates": [[lat, lon], ...], "distance_m",
    "confidence" (0-1), "used": n} when it fits the roads; {"matched": False,
    "used": n} when OSRM finds no road path through them (a phantom trail);
    None when it cannot be asked (no OSRM, OSRM down, under two fixes), which
    callers must not take as "no road"."""
    base = (osrm_url or "").rstrip("/")
    fixes = usable(points)
    if not base or len(fixes) < 2:
        return None
    line: list = []
    distance = 0.0
    weighted = 0.0
    try:
        async with aiohttp.ClientSession() as session:
            # Consecutive chunks share their boundary fix so the path is continuous.
            start = 0
            while start < len(fixes) - 1:
                chunk = fixes[start:start + CHUNK]
                part, dist, w = await _match_chunk(session, base, chunk)
                line.extend(part if not line else part[1:])
                distance += dist
                weighted += w
                start += CHUNK - 1
    except _NoMatch as e:
        log.info("[map_match] no road path through %d fixes (%s)", len(fixes), e)
        return {"matched": False, "used": len(fixes)}
    except Exception as e:  # unreachable, timeout, bad JSON: serve raw points instead
        log.warning("[map_match] OSRM unavailable: %s", e)
        return None
    if len(line) < 2:
        return {"matched": False, "used": len(fixes)}
    return {
        "matched": True,
        "coordinates": [[lat, lon] for lon, lat in line],
        "distance_m": distance,
        "confidence": (weighted / distance) if distance > 0 else 0.0,
        "used": len(fixes),
    }


async def route(a: tuple[float, float], b: tuple[float, float], osrm_url: str | None,
                timeout_s: float = 5.0) -> dict | None:
    """Fastest road route from a to b ((lat, lon) each): {"distance_m",
    "duration_s"}, or None when OSRM cannot be asked or finds no route."""
    base = (osrm_url or "").rstrip("/")
    if not base:
        return None
    url = f"{base}/route/v1/driving/{a[1]:.6f},{a[0]:.6f};{b[1]:.6f},{b[0]:.6f}"
    try:
        async with aiohttp.ClientSession() as session, session.get(
                url, params={"overview": "false"}, timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
            data = await resp.json(content_type=None)
    except Exception as e:
        log.info("[map_match] route unavailable: %s", e)
        return None
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    r = data["routes"][0]
    return {"distance_m": float(r.get("distance") or 0.0), "duration_s": float(r.get("duration") or 0.0)}


async def table(sources: list[tuple[float, float]], destination: tuple[float, float],
                osrm_url: str | None) -> list[float | None] | None:
    """Drive time (s) from each source to one destination (OSRM table), None
    for a source with no route; None when OSRM cannot be asked."""
    base = (osrm_url or "").rstrip("/")
    if not base or not sources:
        return None
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in [*sources, destination])
    params = {"sources": ";".join(str(i) for i in range(len(sources))),
              "destinations": str(len(sources)), "annotations": "duration"}
    try:
        async with aiohttp.ClientSession() as session, session.get(
                f"{base}/table/v1/driving/{coords}", params=params,
                timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json(content_type=None)
    except Exception as e:
        log.info("[map_match] table unavailable: %s", e)
        return None
    if data.get("code") != "Ok":
        return None
    return [row[0] if row and row[0] is not None else None for row in data.get("durations") or []]
