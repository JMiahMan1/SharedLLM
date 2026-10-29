"""Stays someone makes at a spot that is not a saved place.

Wander offers to save a place once a person has spent a long time at the same
spot on more than one occasion (30 minutes, twice, by default). Nothing is
recorded until a stay lasts long enough, and a suggestion is only ever a
prompt - the person adds the place themselves, or tells us to stop asking
about that spot.

Only Redis lives here; ``main.py`` records stays from the stop tracker and
serves the suggestions.
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Any

#: Redis hash per person: a rounded spot -> the stays seen there, as JSON.
VISITS_KEY = "geo:visits:{user}"
#: A stay has to last this long before it is worth remembering at all.
MIN_STAY_S = float(os.environ.get("VISIT_MIN_STAY_MINUTES", "30")) * 60
#: How many separate stays turn a spot into a suggestion.
MIN_VISITS = int(os.environ.get("VISIT_SUGGEST_AFTER", "2"))
#: Two stays are "the same spot" when they are within this many metres.
MATCH_RADIUS_M = float(os.environ.get("VISIT_MATCH_RADIUS_M", "150"))
MAX_ENTRIES = 50
_EARTH_RADIUS_M = 6371000.0


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _entry(raw: Any) -> dict[str, Any] | None:
    try:
        visit = json.loads(_decode(raw))
    except (TypeError, ValueError):
        return None
    return visit if isinstance(visit, dict) else None


async def list_for(r, user: str) -> list[dict[str, Any]]:
    """Every spot this person has stayed at, newest first."""
    raw = await r.hgetall(VISITS_KEY.format(user=user))
    visits = [visit for visit in (_entry(value) for value in raw.values()) if visit]
    return sorted(visits, key=lambda visit: float(visit.get("last_ts", 0)), reverse=True)


def _field(lat: float, lon: float) -> str:
    return f"{lat:.5f},{lon:.5f}"


async def note_stay(
    r,
    user: str,
    lat: float,
    lon: float,
    seconds: float,
    since: float,
    ts: float | None = None,
) -> dict[str, Any] | None:
    """Record one stay, counting a spot only once per ``since`` stamp.

    A stay shorter than ``MIN_STAY_S`` is ignored, and a spot already counted
    for this same ``since`` is not counted twice (fixes race, so the sweep may
    run as often as it likes).
    """
    if seconds < MIN_STAY_S:
        return None
    now = time.time() if ts is None else ts
    key = VISITS_KEY.format(user=user)
    visits = await list_for(r, user)
    match = next(
        (visit for visit in visits if _distance_m(lat, lon, float(visit["lat"]), float(visit["lon"])) <= MATCH_RADIUS_M),
        None,
    )
    if match is not None:
        if match.get("last_since") == since:
            return match
        match["count"] = int(match.get("count", 0)) + 1
        match["total_s"] = float(match.get("total_s", 0.0)) + seconds
        match["last_since"] = since
        match["last_ts"] = now
        match["dismissed"] = False
        await r.hset(key, _field(float(match["lat"]), float(match["lon"])), json.dumps(match))
        return match
    entry = {
        "lat": lat,
        "lon": lon,
        "count": 1,
        "total_s": seconds,
        "last_since": since,
        "last_ts": now,
        "dismissed": False,
    }
    await r.hset(key, _field(lat, lon), json.dumps(entry))
    await _trim(r, key)
    return entry


async def _trim(r, key: str) -> None:
    """Keep only the most recent ``MAX_ENTRIES`` spots per person."""
    raw = await r.hgetall(key)
    if len(raw) <= MAX_ENTRIES:
        return
    entries = sorted(
        ((field, _entry(value)) for field, value in raw.items()),
        key=lambda item: float((item[1] or {}).get("last_ts", 0)),
    )
    for field, _ in entries[: len(entries) - MAX_ENTRIES]:
        await r.hdel(key, _decode(field))


async def suggestions(r, user: str) -> list[dict[str, Any]]:
    """Spots worth offering to save: long stays, seen ``MIN_VISITS`` times or more."""
    return [
        visit
        for visit in await list_for(r, user)
        if not visit.get("dismissed") and int(visit.get("count", 0)) >= MIN_VISITS
    ]


async def dismiss(r, user: str, lat: float, lon: float) -> bool:
    """Stop asking about the spot nearest ``lat``/``lon``. True when one matched."""
    key = VISITS_KEY.format(user=user)
    for visit in await list_for(r, user):
        if _distance_m(lat, lon, float(visit["lat"]), float(visit["lon"])) <= MATCH_RADIUS_M:
            visit["dismissed"] = True
            await r.hset(key, _field(float(visit["lat"]), float(visit["lon"])), json.dumps(visit))
            return True
    return False
