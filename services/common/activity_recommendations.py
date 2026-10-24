"""Activity recommendations (Phase 11, step 3).

Jarvis reads the shape of a member's week on the phone -- how late the last
use was, how long the days were -- and suggests one real-world thing to do,
with a plain reason. It suggests, it never scolds, and it never repeats the
numbers back: the member sees "a phone-free wind-down", not their minutes.

The raw usage stays parents-only; only the derived suggestion is returned to
the member it is for.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

# A night counts as late when the last use is at or after this hour (local).
LATE_HOUR = 22
LATE_NIGHTS = 2  # of the reported days
# A heavy day: this much screen time.
HEAVY_DAY_MS = 4 * 60 * 60 * 1000
HEAVY_DAYS = 3

ACTIVITIES: dict[str, dict[str, Any]] = {
    "late nights": {
        "activity": "A phone-free wind-down",
        "style": "phone charging outside the bedroom, a Psalm and a prayer before sleep",
        "reason": "Rest is a gift (Psalm 4:8); the last half hour before sleep is easier without a screen.",
    },
    "long days": {
        "activity": "Time outside together",
        "style": "a walk, a bike ride or a game in the yard",
        "reason": "A break in the day for the body and the people next to you; the phone can wait.",
    },
}


def _local_hour(epoch_ms: int | float, tz: ZoneInfo) -> int:
    return datetime.fromtimestamp(epoch_ms / 1000, tz).hour


def recommend_activity(usage_days: Iterable[dict[str, Any]], tz: str = "UTC") -> dict[str, Any] | None:
    """One activity suggestion from the week's pattern, or None if the week looks fine.

    Late nights come first: sleep is the habit that moves everything else.
    """
    zone = ZoneInfo(tz or "UTC")
    days = [d for d in usage_days or [] if isinstance(d, dict)]
    late = 0
    for day in days:
        last = day.get("last_use")
        if last:
            hour = _local_hour(last, zone)
            # Past midnight counts too (00:00-04:59 is still the night before).
            if hour >= LATE_HOUR or hour < 5:
                late += 1
    heavy = sum(1 for d in days if (d.get("millis") or 0) >= HEAVY_DAY_MS)
    if late >= LATE_NIGHTS:
        theme, because = "late nights", "late nights on the phone this week"
    elif heavy >= HEAVY_DAYS:
        theme, because = "long days", "long days on the phone this week"
    else:
        return None
    rec = ACTIVITIES[theme]
    return {
        "theme": theme,
        "activity": rec["activity"],
        "style": rec["style"],
        "reason": rec["reason"],
        "matched_on": because,
        "kind": "activity",
        "redeemed": False,
        "redirect_to": rec["activity"],
    }
