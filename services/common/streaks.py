"""Streaks: runs of days, strict or forgiving. Shared by geo and health.

``longest_run`` is the strict count of consecutive calendar days (geo's step
goal achievements). ``streak`` is the forgiving one the Health hub shows:
"Failed" days and red warnings make people quit (docs/HEALTH_HUB.md,
principle 5), so a streak here survives a single missed day in any seven --
a rest day -- and today never breaks it while it is still today. Two misses
within a week end it. Only logged days count; a rest day bridges a streak but
never adds to it.
"""
from __future__ import annotations

import itertools
from collections.abc import Iterable
from datetime import date, timedelta

REST_WINDOW = 7


def week_start(day: date) -> date:
    """The Monday of the week ``day`` falls in (every hub week runs Monday to Sunday)."""
    return day - timedelta(days=day.weekday())


def _walk(done: set[str], end: date, *, open_today: bool) -> tuple[int, int]:
    """(days counted, rest days used) for the streak ending on ``end``."""
    count = 0
    rests: list[int] = []
    offset = 0
    if open_today and end.isoformat() not in done:
        offset = 1  # today is still open: start from yesterday
    pending_rest: int | None = None
    while True:
        day = (end - timedelta(days=offset)).isoformat()
        if day in done:
            if pending_rest is not None:
                rests.append(pending_rest)
                pending_rest = None
            count += 1
        else:
            recent = [rest for rest in rests if offset - rest < REST_WINDOW]
            if pending_rest is not None or recent:
                break
            pending_rest = offset
        offset += 1
        if offset > 400:
            break
    return count, len(rests)


def streak(done: set[str], today: date, *, history_days: int = 90) -> dict:
    """The current streak, the best in the window, and whether today is logged."""
    current, rest_days = _walk(done, today, open_today=True)
    best = current
    for back in range(1, history_days):
        end = today - timedelta(days=back)
        if end.isoformat() in done:
            best = max(best, _walk(done, end, open_today=False)[0])
    return {"current": current, "best": best, "rest_days": rest_days, "today_done": today.isoformat() in done}


def longest_run(days: Iterable[date]) -> int:
    """Longest run of consecutive calendar days among ``days`` (no forgiveness)."""
    ordered = sorted(set(days))
    if not ordered:
        return 0
    best = current = 1
    for prev, nxt in itertools.pairwise(ordered):
        current = current + 1 if (nxt - prev) == timedelta(days=1) else 1
        best = max(best, current)
    return best
