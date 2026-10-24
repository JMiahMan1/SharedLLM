"""Screen-time budgets and routines (Phase 11, step 4): the one rule.

Given a member's rules (set by a parent), today's phone use, any bonus minutes
a parent granted today and the household's local time, decide how much is
left and whether the phone should be locked now, and why. Pure on purpose:
the gateway's status route, the parents' view and the phone all read the same
answer, and every case is testable without a clock or a device.

Semantics follow Family Link and Qustodio:
- the daily limit resets at local midnight; a weekend limit replaces it on
  Saturday and Sunday when set; bonus minutes extend today's limit only;
- bedtime and school time are windows (bedtime may cross midnight) during
  which the phone is locked whatever is left of the limit;
- a parent's pause locks the phone until it is lifted or runs out;
- apps on the always-allowed list (phone, messages, Jarvis) are never locked.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SCHOOL_DAYS_DEFAULT = ("mon", "tue", "wed", "thu", "fri")
# Never locked, so a locked phone can still call home, text a parent and ask
# Jarvis for more time.
ALWAYS_ALLOWED_DEFAULT = (
    "com.jarvisos.app",
    "com.google.android.dialer",
    "com.android.dialer",
    "com.google.android.apps.messaging",
    "com.android.mms",
    "com.android.emergency",
)


def parse_hhmm(value: str | None) -> time | None:
    """'21:30' -> time(21, 30); anything else (empty, malformed) -> None."""
    if not value:
        return None
    try:
        hours, minutes = (int(part) for part in str(value).split(":", 1))
        return time(hours, minutes)
    except (ValueError, TypeError):
        return None


def _window(now: datetime, start: time | None, end: time | None) -> tuple[bool, datetime | None]:
    """Whether ``now`` is inside [start, end) and, if so, when it ends.

    A window whose start is after its end runs overnight (21:00-07:00).
    """
    if start is None or end is None or start == end:
        return False, None
    today = now.date()
    at = now.time()
    if start < end:
        if start <= at < end:
            return True, datetime.combine(today, end, now.tzinfo)
        return False, None
    if at >= start:
        return True, datetime.combine(today + timedelta(days=1), end, now.tzinfo)
    if at < end:
        return True, datetime.combine(today, end, now.tzinfo)
    return False, None


def evaluate(rules: dict[str, Any] | None, used_ms: int, bonus_min: int, now: datetime) -> dict[str, Any]:
    """The member's screen-time state right now.

    ``now`` must be timezone-aware in the household's zone. Returns the limit,
    use and remaining minutes (None when there is no limit), the active
    routine, and ``locked`` with a ``reason`` and an ``until`` (ISO) when the
    lock lifts on its own (the limit's lock lifts at midnight).
    """
    rules = rules or {}
    weekday = WEEKDAYS[now.weekday()]
    weekend = weekday in ("sat", "sun")
    limit = rules.get("daily_limit_min")
    if weekend and rules.get("weekend_limit_min") is not None:
        limit = rules.get("weekend_limit_min")
    used_min = max(0, int(used_ms or 0)) // 60000
    bonus = max(0, int(bonus_min or 0))
    remaining = None if limit is None else max(0, int(limit) + bonus - used_min)
    midnight = datetime.combine(now.date() + timedelta(days=1), time(0, 0), now.tzinfo)

    bedtime, bedtime_until = _window(now, parse_hhmm(rules.get("bedtime_start")), parse_hhmm(rules.get("bedtime_end")))
    school_days = rules.get("school_days") or list(SCHOOL_DAYS_DEFAULT)
    school, school_until = (False, None)
    if weekday in school_days:
        school, school_until = _window(now, parse_hhmm(rules.get("school_start")), parse_hhmm(rules.get("school_end")))

    paused_until = None
    raw_pause = rules.get("paused_until")
    if raw_pause:
        try:
            paused_until = datetime.fromisoformat(str(raw_pause))
            if paused_until.tzinfo is None:
                paused_until = paused_until.replace(tzinfo=now.tzinfo)
        except ValueError:
            paused_until = None
    paused = bool(rules.get("paused")) or (paused_until is not None and now < paused_until)

    if paused:
        locked, reason, until = True, "paused", (paused_until if paused_until and now < paused_until else None)
    elif bedtime:
        locked, reason, until = True, "bedtime", bedtime_until
    elif school:
        locked, reason, until = True, "school", school_until
    elif remaining == 0:
        locked, reason, until = True, "limit", midnight
    else:
        locked, reason, until = False, None, None

    return {
        "limit_min": None if limit is None else int(limit),
        "used_min": used_min,
        "bonus_min": bonus,
        "remaining_min": remaining,
        "bedtime_active": bedtime,
        "school_active": school,
        "paused": paused,
        "locked": locked,
        "reason": reason,
        "until": until.isoformat() if until else None,
        "resets_at": midnight.isoformat(),
        "always_allowed": list(rules.get("always_allowed") or ALWAYS_ALLOWED_DEFAULT),
    }
