"""Driving events and the driver report (Life360's driver report).

The phone detects events while it is driving (hard braking and rapid
acceleration from one-second GPS fixes, a stretch over 80 mph, the phone in
use while moving) and sends them with its activity beacons; the gateway
hands the ``driving`` ones to geo. They are kept per person for
``KEEP_DAYS`` and counted against the trips they fall in.
"""
from __future__ import annotations

import json
import time
from typing import Any

KEY = "geo:driving:{user}"
KEEP_DAYS = 90
EVENTS = ("hard_brake", "rapid_accel", "high_speed", "phone_use")


def clean_event(event: Any, detail: Any) -> dict[str, Any] | None:
    """One event as stored, or None when it is not one geo keeps."""
    if event not in EVENTS or not isinstance(detail, dict):
        return None
    try:
        at = float(detail.get("at")) / 1000.0  # the phone sends epoch ms
    except (TypeError, ValueError):
        return None
    now = time.time()
    if at > now + 60 or at < now - KEEP_DAYS * 86400:
        return None
    out: dict[str, Any] = {"event": event, "t": at}
    for key in ("lat", "lon", "speed_mph", "from_mph", "to_mph", "duration_s"):
        value = detail.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = round(float(value), 5 if key in ("lat", "lon") else 1)
    return out


async def add(r, user: str, entry: dict[str, Any]) -> None:
    key = KEY.format(user=user)
    await r.zadd(key, {json.dumps(entry, sort_keys=True): entry["t"]})
    await r.zremrangebyscore(key, 0, time.time() - KEEP_DAYS * 86400)


async def between(r, user: str, start: float, end: float) -> list[dict[str, Any]]:
    out = []
    for raw in await r.zrangebyscore(KEY.format(user=user), start, end):
        try:
            out.append(json.loads(raw))
        except (TypeError, ValueError):
            continue
    return out


def counts(events: list[dict[str, Any]]) -> dict[str, Any]:
    """How many of each, and the minutes the phone was in use while moving."""
    out: dict[str, Any] = {name: 0 for name in EVENTS}
    phone_s = 0.0
    for e in events:
        if e.get("event") in out:
            out[e["event"]] += 1
        if e.get("event") == "phone_use":
            phone_s += float(e.get("duration_s") or 0)
    out["phone_use_min"] = round(phone_s / 60.0, 1)
    return out


def report(trips: list[dict[str, Any]], events: list[dict[str, Any]], days: int) -> dict[str, Any]:
    """The driver report over ``days``: totals and each trip's events."""
    drives = [t for t in trips if (t.get("activity_type") or "driving") == "driving"]
    per_trip = []
    for t in drives:
        start, end = float(t.get("start_time") or 0), float(t.get("end_time") or t.get("start_time") or 0)
        inside = [e for e in events if start - 30 <= e["t"] <= end + 30]
        per_trip.append({
            "id": t.get("id"),
            "start_time": start,
            "end_time": end,
            "distance_miles": t.get("distance_miles"),
            "top_speed_mph": t.get("top_speed_mph"),
            "start_location": t.get("start_location"),
            "end_location": t.get("end_location"),
            "events": counts(inside),
        })
    return {
        "days": days,
        "trips": len(drives),
        "miles": round(sum(float(t.get("distance_miles") or 0) for t in drives), 1),
        "drive_minutes": round(sum(float(t.get("duration_seconds") or 0) for t in drives) / 60.0),
        "top_speed_mph": max((float(t.get("top_speed_mph") or 0) for t in drives), default=0.0),
        "events": counts(events),
        "per_trip": sorted(per_trip, key=lambda t: t["start_time"], reverse=True),
    }
