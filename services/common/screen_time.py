"""Screen-time budgets and routines (Phase 11, step 4): the one rule.

Given a member's rules (set by a parent), today's phone use, any bonus minutes
a parent granted today and the household's local time, decide how much is
left and whether the phone should be locked now, and why. Pure on purpose:
the gateway's status route, the parents' view and the phone all read the same
answer, and every case is testable without a clock or a device.

Semantics follow Family Link and Qustodio:
- the daily limit resets at local midnight; a weekend limit replaces it on
  Saturday and Sunday when set, and a day of the weekly schedule
  (``day_limits``) replaces both on that day; bonus minutes extend today's limit only;
- bedtime and school time are windows (bedtime may cross midnight) during
  which the phone is locked whatever is left of the limit;
- a parent's pause locks the phone until it is lifted or runs out;
- apps on the always-allowed list (phone, messages, Jarvis) are never locked;
- app control (step 5): a parent can block an app outright, or give it its own
  daily minutes; an app past its minutes is blocked until midnight while the
  rest of the phone keeps working;
- the next lock is named ahead of time (``next_lock_at``/``next_lock_reason``)
  so the phone can give Family Link's one-minute warning before it lands;
- install approvals and permission gating are decided on the phone (only it
  knows which apps are new and which dialog is open); the server passes the
  parents' answers through (``approvals`` from identity);
- a parent's "allow for a while" for a blocked site lives in
  ``web_filter.allow_until`` ({domain: ISO}); ``active_allow`` is the list
  the phone's filter honours now.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SCHOOL_DAYS_DEFAULT = ("mon", "tue", "wed", "thu", "fri")
# Never locked, so a locked phone can still call home, text a parent and ask
# Jarvis for more time. The Samsung and other OEM dialer/messaging packages are
# here too, since the always-allowed list has to name the package the phone
# actually shows for a call or a text.
#
# The core below is unremovable: a parent's own list adds to it, but can never
# take away the ability to call for help or to ask for more time from the lock
# screen itself. The messaging apps are only in the default, so a parent who
# names their own list still decides whether texting is allowed.
NEVER_BLOCKED_CORE = (
    "com.jarvisos.app",
    "com.google.android.dialer",
    "com.android.dialer",
    "com.samsung.android.dialer",
    "com.android.emergency",
    "com.android.incallui",
    "com.samsung.android.incallui",
    "com.android.server.telecom",
)
ALWAYS_ALLOWED_DEFAULT = NEVER_BLOCKED_CORE + (
    "com.google.android.apps.messaging",
    "com.android.mms",
    "com.samsung.android.messaging",
)


def always_allowed(rules: dict[str, Any] | None) -> list[str]:
    """The packages never blocked: a parent's list, plus the safety core.

    A list a parent saved used to replace the default outright, so customising
    it could drop the dialer and leave a locked phone unable to call 911. The
    core is now always present; the parent's list adds to it.
    """
    listed = (rules or {}).get("always_allowed")
    out = list(NEVER_BLOCKED_CORE)
    for pkg in listed if isinstance(listed, (list, tuple)) else ():
        if isinstance(pkg, str) and pkg and pkg not in out:
            out.append(pkg)
    return out


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


# The kinds of permission a parent can ask to approve first (Family Link's
# per-permission controls). The phone recognises Android's grant dialog for
# each one; anything not listed here is never gated.
PERMISSION_GROUPS = ("location", "camera", "microphone", "contacts", "sms", "phone", "photos", "calendar", "nearby")


def next_lock(rules: dict[str, Any], now: datetime, remaining_min: int | None) -> tuple[datetime | None, str | None]:
    """When the phone next locks on its own (within a day) and why.

    Bedtime and school start at their set times; the daily limit runs out
    after the minutes left if the phone stays in use. The earliest wins.
    """
    candidates: list[tuple[datetime, str]] = []

    def upcoming(start: time | None, days_ok) -> datetime | None:
        if start is None:
            return None
        for offset in (0, 1):
            day = now.date() + timedelta(days=offset)
            at = datetime.combine(day, start, now.tzinfo)
            if at > now and days_ok(WEEKDAYS[day.weekday()]):
                return at
        return None

    bed = upcoming(parse_hhmm(rules.get("bedtime_start")), lambda _d: parse_hhmm(rules.get("bedtime_end")) is not None)
    if bed:
        candidates.append((bed, "bedtime"))
    school_days = rules.get("school_days") or list(SCHOOL_DAYS_DEFAULT)
    if parse_hhmm(rules.get("school_end")) is not None:
        school = upcoming(parse_hhmm(rules.get("school_start")), lambda d: d in school_days)
        if school:
            candidates.append((school, "school"))
    if remaining_min is not None and remaining_min > 0:
        candidates.append((now + timedelta(minutes=remaining_min), "limit"))
    if not candidates:
        return None, None
    at, reason = min(candidates, key=lambda c: c[0])
    return at, reason


def active_allow(web_filter: dict[str, Any] | None, now: datetime) -> list[str]:
    """The parent's allow list plus the sites allowed for a while that have not run out."""
    wf = web_filter or {}
    out = list(wf.get("allow") or [])
    for domain, until in (wf.get("allow_until") or {}).items():
        try:
            ends = datetime.fromisoformat(str(until))
        except ValueError:
            continue
        if ends.tzinfo is None:
            ends = ends.replace(tzinfo=now.tzinfo)
        if ends > now and domain not in out:
            out.append(domain)
    return out


def apps_state(rules: dict[str, Any], apps_used_ms: dict[str, int] | None) -> tuple[list[str], dict[str, int]]:
    """Which apps are blocked now, and minutes left for each app with a limit.

    An always-allowed app is never blocked, whatever the lists say.
    """
    allowed = set(always_allowed(rules))
    used = apps_used_ms or {}
    blocked = [pkg for pkg in rules.get("blocked_apps") or [] if pkg not in allowed]
    left: dict[str, int] = {}
    for pkg, limit in (rules.get("app_limits") or {}).items():
        if pkg in allowed:
            continue
        remaining = max(0, int(limit) - max(0, int(used.get(pkg) or 0)) // 60000)
        left[pkg] = remaining
        if remaining == 0 and pkg not in blocked:
            blocked.append(pkg)
    return blocked, left


def evaluate(
    rules: dict[str, Any] | None,
    used_ms: int,
    bonus_min: int,
    now: datetime,
    apps_used_ms: dict[str, int] | None = None,
) -> dict[str, Any]:
    """The member's screen-time state right now.

    ``now`` must be timezone-aware in the household's zone; ``apps_used_ms``
    is today's foreground time per package. Returns the limit,
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
    # The weekly schedule names a day's own limit, which wins over both.
    if (rules.get("day_limits") or {}).get(weekday) is not None:
        limit = rules["day_limits"][weekday]
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
    blocked_apps, app_minutes_left = apps_state(rules, apps_used_ms)
    lock_at, lock_reason = (None, None) if locked else next_lock(rules, now, remaining)

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
        "always_allowed": always_allowed(rules),
        "blocked_apps": blocked_apps,
        "app_minutes_left": app_minutes_left,
        "next_lock_at": lock_at.isoformat() if lock_at else None,
        "next_lock_reason": lock_reason,
        # New apps wait for a parent's OK unless the parents switched that off.
        "install_approval": rules.get("install_approval") is not False,
        "permission_gating": [g for g in rules.get("permission_gating") or [] if g in PERMISSION_GROUPS],
        "web_allow": active_allow(rules.get("web_filter"), now),
    }
