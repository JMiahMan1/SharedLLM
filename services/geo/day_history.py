"""One person's day on the map: where they went and where they stayed.

Life360 calls it location history. Geo already keeps 30 days of breadcrumbs
per person (``geo:history:<user>``); this turns one local day of them into
what Wander draws: the path, thinned for the map, and the stops along it,
places someone stayed at least ``STOP_MIN_S`` within ``STOP_RADIUS_M``.

Pure functions only; ``main.py`` reads Redis, names the stops (saved places
first, then a street address) and checks consent.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

#: Staying within this distance counts as being in one place.
STOP_RADIUS_M = 150.0
#: How long someone must stay for it to be a stop on the timeline.
STOP_MIN_S = 10 * 60
#: Most points the path carries to the map; longer days are thinned evenly.
MAX_PATH_POINTS = 800


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def day_bounds(day: date, tz: str) -> tuple[float, float]:
    """Epoch seconds of the local day's first and last moment."""
    zone = ZoneInfo(tz)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start.timestamp(), end.timestamp()


def thin(points: list[dict[str, Any]], limit: int = MAX_PATH_POINTS) -> list[dict[str, Any]]:
    """At most ``limit`` points, evenly spaced, always keeping the last."""
    if len(points) <= limit:
        return points
    step = math.ceil(len(points) / limit)
    out = points[::step]
    if out[-1] is not points[-1]:
        out.append(points[-1])
    return out


def stops(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Where someone stayed: runs of fixes within STOP_RADIUS_M of the run's
    first fix lasting at least STOP_MIN_S. Each stop is its centre (the mean
    of its fixes), when it began and ended, and how many fixes it holds.

    A gap in the fixes inside a run (the phone slept at home) still counts as
    staying, as long as the next fix is still there.
    """
    found: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []

    def close_run() -> None:
        if len(run) >= 2 and float(run[-1]["t"]) - float(run[0]["t"]) >= STOP_MIN_S:
            found.append({
                "lat": sum(float(p["lat"]) for p in run) / len(run),
                "lon": sum(float(p["lon"]) for p in run) / len(run),
                "since": float(run[0]["t"]),
                "until": float(run[-1]["t"]),
                "fixes": len(run),
            })

    for point in points:
        if run and _distance_m(float(run[0]["lat"]), float(run[0]["lon"]), float(point["lat"]), float(point["lon"])) > STOP_RADIUS_M:
            close_run()
            run = []
        run.append(point)
    close_run()
    return found


def timeline(day_stops: list[dict[str, Any]], trips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stops and trips in the order they happened, each tagged with its kind."""
    entries = [{"kind": "stop", "at": s["since"], **s} for s in day_stops]
    entries += [{"kind": "trip", "at": float(t.get("start_time") or 0), **t} for t in trips]
    return sorted(entries, key=lambda e: e["at"])
