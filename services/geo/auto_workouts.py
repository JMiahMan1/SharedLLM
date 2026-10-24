"""Detect workouts from the location breadcrumb trail.

The manual workout buttons are precise but easy to forget. This module turns a
walk, run or ride that the phone already recorded into a candidate workout.
It is deliberately conservative: a candidate needs several minutes of
consistent movement, must not overlap a drive or another workout, and can be
dismissed by the person afterwards. A false negative is much better than a
false "workout" every time someone walks to the car.
"""
from __future__ import annotations

import itertools
import math
from collections.abc import Iterable
from typing import Any

#: Walking, running and cycling are the activities we can tell apart from GPS
#: speed alone. Mountain biking / dirt biking / horseback riding need terrain
#: or sensor context the phone does not give us here.
WALKING_RANGE = (0.8, 4.6)
RUNNING_RANGE = (4.6, 9.5)
CYCLING_RANGE = (9.5, 22.0)
MAX_GAP_S = 600
PAUSE_GRACE_S = 180
MIN_POINTS = 4
#: duration seconds, distance miles
MINIMUMS = {
    "walking": (10 * 60, 0.35),
    "running": (8 * 60, 0.50),
    "cycling": (10 * 60, 1.50),
}


def _value(point: Any, *names: str, default=None):
    if isinstance(point, dict):
        for name in names:
            if name in point and point[name] is not None:
                return point[name]
        return default
    for name in names:
        if hasattr(point, name):
            value = getattr(point, name)
            if value is not None:
                return value
    return default


def _point_time(point: Any) -> float | None:
    try:
        return float(_value(point, "t", "timestamp", "time"))
    except (TypeError, ValueError):
        return None


def _coordinates(point: Any) -> tuple[float, float] | None:
    try:
        return float(_value(point, "lat", "latitude")), float(_value(point, "lon", "lng", "longitude"))
    except (TypeError, ValueError):
        return None


def _meters(a: Any, b: Any) -> float:
    ca, cb = _coordinates(a), _coordinates(b)
    if ca is None or cb is None:
        return 0.0
    lat1, lon1 = map(math.radians, ca)
    lat2, lon2 = map(math.radians, cb)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6_371_000 * math.asin(min(1.0, math.sqrt(h)))


def _speed_mph(a: Any, b: Any, dt: float) -> float:
    if dt <= 0:
        return 0.0
    return (_meters(a, b) / dt) * 2.2369362920544


def _activity_for(mph: float) -> str | None:
    if WALKING_RANGE[0] <= mph < WALKING_RANGE[1]:
        return "walking"
    if RUNNING_RANGE[0] <= mph < RUNNING_RANGE[1]:
        return "running"
    if CYCLING_RANGE[0] <= mph <= CYCLING_RANGE[1]:
        return "cycling"
    return None


def _segment_kind(a: Any, b: Any) -> tuple[str, float, float]:
    """Return (kind, distance_m, mph) for one pair of fixes.

    kind is an activity, "pause" (slower than a walk), "drive" (faster than a
    bike) or "gap" (missing timestamps / too far apart in time).
    """
    t1, t2 = _point_time(a), _point_time(b)
    if t1 is None or t2 is None:
        return "gap", 0.0, 0.0
    dt = t2 - t1
    if dt <= 0:
        return "gap", 0.0, 0.0
    distance_m = _meters(a, b)
    if dt > MAX_GAP_S:
        return "gap", distance_m, 0.0
    mph = (distance_m / dt) * 2.2369362920544
    if mph < WALKING_RANGE[0]:
        return "pause", distance_m, mph
    if mph > CYCLING_RANGE[1]:
        return "drive", distance_m, mph
    activity = _activity_for(mph)
    return (activity or "pause"), distance_m, mph


def _window(candidate: dict) -> dict | None:
    duration = candidate["end_time"] - candidate["start_time"]
    minimum_duration, minimum_distance = MINIMUMS[candidate["activity_type"]]
    if duration < minimum_duration or candidate["distance_miles"] < minimum_distance:
        return None
    if candidate["points"] < MIN_POINTS:
        return None
    miles = candidate["distance_miles"]
    return {
        "activity_type": candidate["activity_type"],
        "start_time": round(candidate["start_time"], 3),
        "end_time": round(candidate["end_time"], 3),
        "duration_seconds": round(duration),
        "distance_miles": round(miles, 2),
        "top_speed_mph": round(candidate["top_speed_mph"], 1),
        "avg_speed_mph": round(miles / (duration / 3600.0), 1) if duration >= 60 else 0.0,
        "points": candidate["points"],
    }


def overlaps(candidate: dict, others: Iterable[Any], pad_s: float = 60.0) -> bool:
    start = candidate["start_time"]
    end = candidate["end_time"]
    for other in others:
        try:
            other_start = float(_value(other, "start_time"))
            other_end = float(_value(other, "end_time", default=other_start))
        except (TypeError, ValueError):
            continue
        if start < other_end + pad_s and end > other_start - pad_s:
            return True
    return False


def _dismissed(candidate: dict, starts: Iterable[float], tolerance_s: float = 180.0) -> bool:
    start = candidate["start_time"]
    for value in starts:
        try:
            if abs(start - float(value)) <= tolerance_s:
                return True
        except (TypeError, ValueError):
            continue
    return False


def detect_auto_workouts(
    points: Iterable[Any],
    *,
    existing_workouts: Iterable[Any] = (),
    recent_trips: Iterable[Any] = (),
    dismissed_starts: Iterable[float] = (),
) -> list[dict]:
    """Return the workouts visible in a breadcrumb trail.

    ``points`` may be dicts from Redis (``t``, ``lat``, ``lon``) or objects with
    the same attributes. Existing workouts and trips suppress a candidate when
    their windows overlap; dismissed starts suppress the same walk after the
    person says "not a workout".
    """
    ordered = [p for p in points if _point_time(p) is not None and _coordinates(p) is not None]
    ordered.sort(key=lambda p: _point_time(p))
    if len(ordered) < 2:
        return []

    candidates: list[dict] = []
    current: dict | None = None

    def close() -> None:
        nonlocal current
        if current is not None:
            finished = _window(current)
            if finished is not None:
                candidates.append(finished)
        current = None

    for previous, point in itertools.pairwise(ordered):
        kind, distance_m, mph = _segment_kind(previous, point)
        if current is None:
            if kind in MINIMUMS:
                current = {
                    "activity_type": kind,
                    "start_time": _point_time(previous) or 0.0,
                    "end_time": _point_time(point) or 0.0,
                    "distance_miles": distance_m / 1609.344,
                    "top_speed_mph": mph,
                    "points": 2,
                }
            continue

        if kind == current["activity_type"]:
            current["end_time"] = _point_time(point) or current["end_time"]
            current["distance_miles"] += distance_m / 1609.344
            current["top_speed_mph"] = max(current["top_speed_mph"], mph)
            current["points"] += 1
            continue

        if kind == "pause":
            pause_s = (_point_time(point) or current["end_time"]) - current["end_time"]
            if pause_s <= PAUSE_GRACE_S:
                current["end_time"] = _point_time(point) or current["end_time"]
                current["points"] += 1
                continue
            close()
            continue

        # A different activity, a drive or a long gap ends the current window.
        close()
        if kind in MINIMUMS:
            current = {
                "activity_type": kind,
                "start_time": _point_time(previous) or 0.0,
                "end_time": _point_time(point) or 0.0,
                "distance_miles": distance_m / 1609.344,
                "top_speed_mph": mph,
                "points": 2,
            }
    close()

    kept: list[dict] = []
    for candidate in candidates:
        if overlaps(candidate, existing_workouts):
            continue
        if overlaps(candidate, recent_trips):
            continue
        if _dismissed(candidate, dismissed_starts):
            continue
        kept.append(candidate)
    return kept
