"""Workouts recorded elsewhere (Health Connect on the phone) brought into geo.

Geo owns workouts, so an exercise session a watch or another app wrote to
Health Connect lands here rather than in the health store. The rules are
kept pure so they can be tested without Redis:

* Idempotent per ``(user, source, external_id)``: the phone re-reads an
  overlapping window on every sync and the server answers ``duplicate``.
* Only the activity types geo already shows are accepted; anything else is
  refused with a reason instead of being filed under the wrong name.
* A session that overlaps a workout geo already has from another source
  (the same walk tracked by Jarvis's GPS and by a watch) is skipped, using
  the same overlap rule automatic detection uses (``auto_workouts.overlaps``),
  so one walk is never counted twice.
* Steps are never set: the phone's pedometer already counts them per day.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from services.geo.auto_workouts import overlaps

SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MAX_DURATION_S = 24 * 3600
MIN_DURATION_S = 60
METERS_PER_MILE = 1609.344


def workout_id(user: str, source: str, external_id: str) -> str:
    """A stable id, so a replay can never mint a second record."""
    digest = hashlib.sha1(f"{source}:{external_id}".encode()).hexdigest()[:16]
    return f"import_{user}_{digest}"


def duplicate_of(start: float, end: float, source: str, existing: list[dict[str, Any]]) -> dict | None:
    """The workout from another source this session repeats, if any.

    Same rule as automatic detection (``auto_workouts.overlaps``, which pads
    each side by a minute), applied one workout at a time so the answer can
    name the one it repeats.
    """
    window = {"start_time": start, "end_time": end}
    for other in existing:
        if other.get("source") == source:
            continue
        if overlaps(window, [other]):
            return other
    return None


def build(
    item: dict[str, Any],
    *,
    user: str,
    source: str,
    allowed_types: dict[str, dict],
    now: float,
) -> tuple[dict | None, str | None]:
    """The workout record for one imported session, or ``(None, reason)``."""
    activity = str(item.get("activity_type") or "")
    if activity not in allowed_types or activity == "driving":
        return None, "unsupported_type"
    try:
        start = float(item["start_time"])
        end = float(item["end_time"])
    except (KeyError, TypeError, ValueError):
        return None, "invalid_time"
    if not (end > start) or end > now + 300:
        return None, "invalid_time"
    duration = end - start
    if duration < MIN_DURATION_S or duration > MAX_DURATION_S:
        return None, "invalid_duration"

    distance_m = item.get("distance_meters")
    distance_miles = 0.0
    distance_known = False
    if distance_m is not None:
        try:
            meters = float(distance_m)
        except (TypeError, ValueError):
            return None, "invalid_distance"
        if meters < 0 or meters > 500_000:
            return None, "invalid_distance"
        distance_miles = round(meters / METERS_PER_MILE, 2)
        distance_known = True

    external_id = str(item["external_id"])
    seconds = int(duration)
    workout = {
        "id": workout_id(user, source, external_id),
        "user_id": user,
        "user_name": user.title(),
        "activity_type": activity,
        "start_time": start,
        "end_time": end,
        "duration_seconds": seconds,
        "distance_miles": distance_miles,
        "top_speed_mph": 0.0,
        "avg_speed_mph": round(distance_miles / (seconds / 3600.0), 1) if distance_known and seconds >= 60 else 0.0,
        "steps": None,
        "steps_source": None,
        "elevation_gain_ft": None,
        "calories_burned": None,
        "notes": (str(item.get("title") or "").strip() or None),
        "status": "completed",
        "created_at": now,
        "updated_at": now,
        "source": source,
        "external_id": external_id,
        "imported": True,
        "origin": (str(item.get("origin") or "").strip() or None),
    }
    if distance_known:
        workout["distance_source"] = source
    return workout, None
