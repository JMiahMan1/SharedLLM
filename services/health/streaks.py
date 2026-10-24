"""Forgiving streaks: days in a row a habit was logged, with one rest day a week.

"Failed" days and red warnings make people quit (docs/HEALTH_HUB.md,
principle 5), so a streak here survives a single missed day in any seven --
a rest day -- and today never breaks it while it is still today. Two misses
within a week end it. Only logged days count; a rest day bridges a streak but
never adds to it.
"""
from __future__ import annotations

from datetime import date, timedelta

#: key -> (title, what counts). Every habit is fine for a child: none is about
#: calories or weight.
HABITS: dict[str, tuple[str, str]] = {
    "logging": ("Showing up", "Days you logged anything: food, water, sleep or a check-in."),
    "checkin": ("Morning check-in", "Days you answered the morning check-in."),
    "water_goal": ("Water goal", "Days your logged water reached your water goal."),
    "sleep_logged": ("Sleep logged", "Nights you logged your sleep."),
}
RULE = "One missed day in any seven is a rest day and keeps the streak; today counts once it is logged."
REST_WINDOW = 7


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
