"""Active-Zone-like minutes for a Monday-to-Sunday week, from what was recorded.

Four sources, best first, never mixed into a guess:

* **device** -- ``active_minutes`` readings a watch or Health Connect posted.
  A day with any of these takes the device's sum and nothing else, because
  the device measured it directly.
* **heart_rate** -- moment-to-moment heart-rate readings. Each minute's
  highest reading counts once at 64-76 % of the person's maximum (moderate)
  and twice at 77 % or more (vigorous), the way Fitbit's Active Zone Minutes
  weight cardio. The maximum is 220 minus age, so no birth year means no
  heart-rate minutes (and the reason is said).
* **workouts** -- the duration of each workout geo recorded, split into the
  hours it covered.
* **steps** -- geo's hourly steps (the highest device per hour, never a sum
  of overlapping devices). An hour counts its steps beyond 1,000 at 100 steps
  a minute -- the brisk-walking cadence -- up to 60. This is an estimate and
  is labelled one.

Per hour the largest of heart-rate, workout and step minutes counts, so one
walk measured three ways is still one walk. A day with nothing recorded is
missing, not zero.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from services.common.streaks import week_start

__all__ = ["compute", "max_heart_rate", "step_minutes", "week_start"]

BASELINE_STEPS_PER_HOUR = 1000
BRISK_STEPS_PER_MINUTE = 100
MODERATE_FRACTION = 0.64
VIGOROUS_FRACTION = 0.77


def _local(value: str | float | int, tz: ZoneInfo) -> datetime | None:
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OSError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def step_minutes(steps: float) -> int:
    """Brisk minutes credited to one hour's steps."""
    return int(max(0, min(60, (steps - BASELINE_STEPS_PER_HOUR) // BRISK_STEPS_PER_MINUTE)))


def max_heart_rate(age: int | None) -> int | None:
    return 220 - age if age and 5 <= age <= 120 else None


def compute(
    *,
    today: date,
    tz: ZoneInfo,
    hourly_steps: dict[str, dict[str, int]] | None,
    workouts: list[dict[str, Any]] | None,
    heart_rate: list[dict[str, Any]] | None,
    device_minutes: list[dict[str, Any]] | None,
    age: int | None,
    target_minutes: int | None,
) -> dict[str, Any]:
    """The week containing ``today`` (Monday first), day by day."""
    start = week_start(today)
    days = [(start + timedelta(days=offset)).isoformat() for offset in range(7)]
    in_week = set(days)
    notes: list[str] = []
    used: set[str] = set()

    # hour key "YYYY-MM-DD:HH" -> minutes, per source.
    steps_by_hour: dict[str, int] = {}
    for per_source in (hourly_steps or {}).values():
        for key, count in (per_source or {}).items():
            if key[:10] in in_week:
                steps_by_hour[key] = max(steps_by_hour.get(key, 0), int(count or 0))
    step_hour_minutes = {key: step_minutes(count) for key, count in steps_by_hour.items()}

    workout_hour_minutes: dict[str, float] = defaultdict(float)
    for workout in workouts or []:
        if workout.get("status") not in (None, "completed"):
            continue
        begin = _local(workout.get("start_time"), tz) if workout.get("start_time") is not None else None
        if begin is None:
            continue
        if workout.get("end_time") is not None:
            end = _local(workout["end_time"], tz)
        elif workout.get("duration_seconds"):
            end = begin + timedelta(seconds=float(workout["duration_seconds"]))
        else:
            end = None
        if end is None or end <= begin:
            continue
        cursor = begin
        while cursor < end:
            hour_end = cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            stop = min(end, hour_end)
            key = f"{cursor.date().isoformat()}:{cursor.hour:02d}"
            if key[:10] in in_week:
                workout_hour_minutes[key] += (stop - cursor).total_seconds() / 60.0
            cursor = stop

    hr_max = max_heart_rate(age)
    hr_hour_minutes: dict[str, int] = defaultdict(int)
    if heart_rate:
        if hr_max is None:
            notes.append("Heart-rate minutes need your birth year in Health → Health.")
        else:
            per_minute: dict[str, float] = {}
            for sample in heart_rate:
                when = _local(sample.get("recorded_at"), tz)
                value = sample.get("value")
                if when is None or not isinstance(value, (int, float)):
                    continue
                key = when.strftime("%Y-%m-%dT%H:%M")
                per_minute[key] = max(per_minute.get(key, 0.0), float(value))
            for minute, bpm in per_minute.items():
                if minute[:10] not in in_week:
                    continue
                credit = 2 if bpm >= VIGOROUS_FRACTION * hr_max else 1 if bpm >= MODERATE_FRACTION * hr_max else 0
                if credit:
                    hr_hour_minutes[f"{minute[:10]}:{minute[11:13]}"] += credit

    device_by_day: dict[str, float] = defaultdict(float)
    for sample in device_minutes or []:
        when = _local(sample.get("recorded_at"), tz)
        value = sample.get("value")
        if when is not None and isinstance(value, (int, float)) and when.date().isoformat() in in_week:
            device_by_day[when.date().isoformat()] += float(value)

    out_days: list[dict[str, Any]] = []
    for day in days:
        if day > today.isoformat():
            out_days.append({"day": day, "minutes": None, "sources": []})
            continue
        if day in device_by_day:
            used.add("device")
            out_days.append({"day": day, "minutes": round(device_by_day[day]), "sources": ["device"]})
            continue
        hours = {key for key in (*step_hour_minutes, *workout_hour_minutes, *hr_hour_minutes) if key[:10] == day}
        # Any steps hour (even a quiet one), workout or zone minute means the
        # day was recorded, so it reads 0 rather than missing.
        recorded = bool(hours)
        total = 0.0
        sources: set[str] = set()
        for key in hours:
            candidates = {
                "heart_rate": float(hr_hour_minutes.get(key, 0)),
                "workouts": workout_hour_minutes.get(key, 0.0),
                "steps": float(step_hour_minutes.get(key, 0)),
            }
            best = max(candidates, key=lambda name: candidates[name])
            if candidates[best] > 0:
                total += candidates[best]
                sources.add(best)
        used |= sources
        out_days.append({"day": day, "minutes": round(total) if recorded else None, "sources": sorted(sources)})

    recorded_days = [entry["minutes"] for entry in out_days if entry["minutes"] is not None]
    total_minutes = sum(recorded_days) if recorded_days else None
    if not recorded_days:
        notes.append("Nothing recorded this week yet: carry your phone, start a workout, or connect a watch.")
    if "steps" in used:
        notes.append("Step minutes are an estimate: steps beyond 1,000 in an hour count at 100 a minute.")
    return {
        "week_start": days[0],
        "week_end": days[-1],
        "target_minutes": target_minutes,
        "total_minutes": total_minutes,
        "percent": (
            min(100, round((total_minutes or 0) * 100 / target_minutes)) if target_minutes and total_minutes is not None else None
        ),
        "days": out_days,
        "sources": sorted(used),
        "estimate": "steps" in used,
        "notes": notes,
    }
