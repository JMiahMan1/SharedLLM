# services/telemetry/schedule.py
"""Cadence math for scheduled report jobs.

A job is (period, local run time, timezone). Periods are daily / weekly /
monthly / yearly and are anchored to the user's wall clock, not to the last run,
so a report for "end of day" lands at the same local time every day regardless
of when the previous run actually executed.
"""
from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PERIODS = ("daily", "weekly", "monthly", "yearly")
PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30, "yearly": 365}
REPORT_TYPES = ("health", "power")


def resolve_timezone(name: str | None) -> ZoneInfo:
    """Resolve a timezone name, falling back to UTC when unknown or missing."""
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def window_for(period: str, now: datetime | None = None, tz: str | None = None) -> tuple[datetime, datetime]:
    """Return the (start, end) UTC window a report of this period covers.

    daily -> today (local), weekly -> last 7 days, monthly -> last 30 days,
    yearly -> last 365 days. Windows are inclusive of the end instant.
    """
    zone = resolve_timezone(tz)
    local_now = (now or datetime.now(UTC)).astimezone(zone)
    days = PERIOD_DAYS.get(period, 1)
    local_start = local_now - timedelta(days=days)
    return local_start.astimezone(UTC), local_now.astimezone(UTC)


def _clamp_day(year: int, month: int, day: int) -> int:
    return min(day, calendar.monthrange(year, month)[1])


def next_run_at(
    period: str,
    run_at: str,
    tz: str | None,
    after: datetime | None = None,
) -> datetime:
    """Next UTC instant at which this job should fire.

    ``run_at`` is a local "HH:MM". For monthly/yearly jobs the anchor day is
    derived from the current local date so a job created on the 31st keeps
    running on the last valid day of shorter months.
    """
    zone = resolve_timezone(tz)
    try:
        hour, minute = (int(part) for part in str(run_at).split(":", 1))
        hour = min(max(hour, 0), 23)
        minute = min(max(minute, 0), 59)
    except (TypeError, ValueError):
        hour, minute = 21, 0

    base_local = (after or datetime.now(UTC)).astimezone(zone)
    anchor_day = base_local.day

    if period == "daily":
        candidate = base_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= base_local:
            candidate += timedelta(days=1)
        return candidate.astimezone(UTC)

    if period == "weekly":
        candidate = base_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= base_local:
            candidate += timedelta(days=7)
        return candidate.astimezone(UTC)

    if period == "monthly":
        year, month = base_local.year, base_local.month
        day = _clamp_day(year, month, anchor_day)
        candidate = base_local.replace(
            year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= base_local:
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
            day = _clamp_day(year, month, anchor_day)
            candidate = base_local.replace(
                year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0
            )
        return candidate.astimezone(UTC)

    if period == "yearly":
        year = base_local.year
        month = base_local.month
        day = _clamp_day(year, month, anchor_day)
        candidate = base_local.replace(
            year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= base_local:
            year += 1
            day = _clamp_day(year, month, anchor_day)
            candidate = base_local.replace(
                year=year, month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0
            )
        return candidate.astimezone(UTC)

    # Unknown period: treat as daily rather than never firing.
    return next_run_at("daily", run_at, tz, after)
