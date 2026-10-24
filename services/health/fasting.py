"""The fasting window: how long since a fast started, against the person's target.

Adults only, and off until the person turns it on (``preferences.SWITCHES``).
A fast is what the person started and stopped, never inferred from food logs:
a log's time is when it was typed, not when the food was eaten. The copy stays
neutral -- elapsed time and the person's own target, no claims about what the
body is doing at a given hour.
"""
from __future__ import annotations

from datetime import datetime

#: Common windows offered in the UI; any whole number of hours in range is accepted.
PRESET_HOURS = (12, 14, 16)
MIN_TARGET_HOURS = 8
MAX_TARGET_HOURS = 24
#: A "fast" longer than this was almost certainly left running by mistake.
MAX_FAST_HOURS = 72


def progress(started: datetime, now: datetime, target_hours: int | None) -> dict:
    """Elapsed minutes and, when a target is set, how far through it."""
    elapsed = max(0, int((now - started).total_seconds() // 60))
    if not target_hours:
        return {"elapsed_minutes": elapsed, "target_minutes": None, "percent": None, "remaining_minutes": None, "reached": False}
    target = target_hours * 60
    return {
        "elapsed_minutes": elapsed,
        "target_minutes": target,
        "percent": min(100, round(elapsed * 100 / target)),
        "remaining_minutes": max(0, target - elapsed),
        "reached": elapsed >= target,
    }
