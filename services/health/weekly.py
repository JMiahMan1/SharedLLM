"""The weekly digest (Health hub Phase 5): one week at a glance.

Takes one record per day (whatever was logged: sleep minutes and score,
calories, water, the check-in's energy and mood) for this week and the week
before, and returns the averages, the best night's sleep and what changed.
A day with nothing logged is missing, never zero: averages are over the days
that have the value, and a value nobody logged is ``None``, not a made-up
number. Calories are left out entirely for a profile in kid mode (the hub's
rule: no calorie framing for kids).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

METRICS = ("sleep_minutes", "sleep_score", "calories", "water_ml", "energy", "mood")

# How far a weekly average must move before it counts as a change worth
# naming; smaller moves are ordinary week-to-week noise.
CHANGE_THRESHOLDS = {
    "sleep_minutes": 15.0,
    "sleep_score": 5.0,
    "calories": 150.0,
    "water_ml": 250.0,
    "energy": 0.5,
    "mood": 0.5,
}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _averages(days: Iterable[dict[str, Any]], metrics: tuple[str, ...]) -> dict[str, float | None]:
    rows = list(days)
    return {m: _mean([float(d[m]) for d in rows if d.get(m) is not None]) for m in metrics}


def _logged(day: dict[str, Any], metrics: tuple[str, ...]) -> bool:
    return any(day.get(m) is not None for m in metrics)


def _best_sleep(days: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The night with the highest score; with no scores, the longest night."""
    scored = [d for d in days if d.get("sleep_score") is not None]
    if scored:
        best = max(scored, key=lambda d: (d["sleep_score"], d.get("sleep_minutes") or 0))
    else:
        timed = [d for d in days if d.get("sleep_minutes") is not None]
        if not timed:
            return None
        best = max(timed, key=lambda d: d["sleep_minutes"])
    return {"day": best["day"], "minutes": best.get("sleep_minutes"), "score": best.get("sleep_score")}


def digest(this_week: list[dict[str, Any]], last_week: list[dict[str, Any]], kid_mode: bool = False) -> dict[str, Any]:
    """The digest for ``this_week`` (oldest day first), compared with ``last_week``.

    ``changes`` names only the metrics logged in both weeks whose average moved
    past its threshold, largest relative move first; an empty list means a
    steady week (or too little logged to compare), never a guess.
    """
    metrics = tuple(m for m in METRICS if not (kid_mode and m == "calories"))
    current = _averages(this_week, metrics)
    previous = _averages(last_week, metrics)

    changes: list[dict[str, Any]] = []
    for metric in metrics:
        now, before = current[metric], previous[metric]
        if now is None or before is None:
            continue
        delta = round(now - before, 1)
        if abs(delta) < CHANGE_THRESHOLDS[metric]:
            continue
        changes.append({
            "metric": metric,
            "this_week": now,
            "last_week": before,
            "delta": delta,
            "direction": "up" if delta > 0 else "down",
        })
    changes.sort(key=lambda c: abs(c["delta"]) / CHANGE_THRESHOLDS[c["metric"]], reverse=True)

    days = [{"day": d["day"], **{m: d.get(m) for m in metrics}} for d in this_week]
    return {
        "start": this_week[0]["day"] if this_week else None,
        "end": this_week[-1]["day"] if this_week else None,
        "metrics": list(metrics),
        "days_logged": sum(1 for d in this_week if _logged(d, metrics)),
        "averages": current,
        "last_week": previous,
        "best_sleep": _best_sleep(this_week),
        "changes": changes,
        "days": days,
    }
