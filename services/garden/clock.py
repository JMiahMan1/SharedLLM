"""The household's "today", for everything in Garden that counts by the day.

Streaks, the morning digest, the rain-skip episode, the Skylight hook's once a
day and the valve's daily cap all ask "what day is it at home?". Garden stores
UTC timestamps, and the UTC day ends at 17:00 in Denver: counted in UTC, a
watering at 18:00 broke a streak and a valve got a fresh allowance in the
evening. The zone is the household's (``services/common/household_tz``: the
Settings value, else ``TZ``, else the home location when enabled). There is no
UTC fallback: an unset or invalid zone raises ``HouseholdTimeZoneError``, which
the service answers with 503 and the message that says how to set it.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from services.common import household_tz


def zone() -> ZoneInfo:
    """The household zone; ``HouseholdTimeZoneError`` when none is configured."""
    return household_tz.zoneinfo()


def now() -> datetime:
    """The household's wall clock."""
    return datetime.now(zone())


def today(at: datetime | None = None) -> date:
    """The household's date at ``at`` (default: now)."""
    moment = at or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(zone()).date()


def today_iso(at: datetime | None = None) -> str:
    return today(at).isoformat()


def local_date(stamp: str) -> date:
    """The household date of a stored ISO timestamp (naive stamps are UTC)."""
    return today(datetime.fromisoformat(stamp))


def day_bounds_utc(day: date | None = None) -> tuple[str, str]:
    """``[start, end)`` of a household day as UTC ISO strings, for stored stamps.

    Stored stamps are ``datetime.now(UTC).isoformat()``; ISO strings in the same
    offset sort like the instants they name, so a range compare is exact.
    """
    day = day or today()
    start = datetime.combine(day, time.min, tzinfo=zone())
    # The next calendar day's midnight, not start + 24 h, so a DST day is right.
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone())
    return start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat()
