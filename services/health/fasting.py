"""The fasting window: how long since a fast started, against the person's target.

Adults only, and off until the person turns it on (``preferences.SWITCHES``).
A fast is what the person started and stopped, never inferred from food logs:
a log's time is when it was typed, not when the food was eaten. The copy stays
neutral -- elapsed time and the person's own target, no claims about what the
body is doing at a given hour.
"""
from __future__ import annotations

from datetime import datetime, timedelta

#: Common windows offered in the UI; any whole number of hours in range is accepted.
PRESET_HOURS = (12, 14, 16)
MIN_TARGET_HOURS = 1
#: The Paschal fast is forty hours; nothing longer is offered as one window.
MAX_TARGET_HOURS = 72
#: A season (Lent, the Daniel fast) is kept for at most this many days.
MAX_DAYS = 50
#: A one-off fast started longer ago than this was almost certainly left running by mistake.
MAX_FAST_HOURS = 72


def progress(started: datetime, now: datetime, target_hours: int | None, ends_at: datetime | None = None,
             days: int | None = None) -> dict:
    """Elapsed and remaining time against the planned end (or the hour target).

    Seconds are kept so the card can count down live between reads; percent
    is floored, so it never shows 100 before the end.
    """
    elapsed_s = max(0, int((now - started).total_seconds()))
    out: dict = {
        "elapsed_minutes": elapsed_s // 60,
        "elapsed_seconds": elapsed_s,
        "target_minutes": None,
        "remaining_minutes": None,
        "remaining_seconds": None,
        "percent": None,
        "reached": False,
        "day": (elapsed_s // 86400) + 1 if days else None,
    }
    if ends_at is None and target_hours:
        ends_at = started + timedelta(hours=target_hours)
    if ends_at is None:
        return out
    total_s = max(1, int((ends_at - started).total_seconds()))
    remaining_s = max(0, int((ends_at - now).total_seconds()))
    out.update(
        target_minutes=total_s // 60,
        remaining_minutes=-(-remaining_s // 60),
        remaining_seconds=remaining_s,
        percent=min(100, (elapsed_s * 100) // total_s),
        reached=remaining_s == 0,
    )
    return out
