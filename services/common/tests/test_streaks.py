"""Forgiving streaks: one rest day a week, today never breaks a streak."""
from __future__ import annotations

from datetime import date, timedelta

from services.common import streaks

TODAY = date(2026, 10, 24)


def _days(*offsets: int) -> set[str]:
    return {(TODAY - timedelta(days=offset)).isoformat() for offset in offsets}


def test_days_in_a_row_count_and_today_counts_once_logged():
    assert streaks.streak(_days(0, 1, 2), TODAY)["current"] == 3
    open_today = streaks.streak(_days(1, 2), TODAY)
    assert open_today["current"] == 2 and open_today["today_done"] is False


def test_one_missed_day_is_a_rest_day():
    result = streaks.streak(_days(0, 1, 3, 4), TODAY)
    assert result == {"current": 4, "best": 4, "rest_days": 1, "today_done": True}
    # Yesterday missed with today still open: the rest day keeps it.
    assert streaks.streak(_days(2, 3, 4), TODAY)["current"] == 3


def test_two_misses_in_a_week_end_the_streak():
    # 0, 1 and 3 count (2 is the rest day); 4 is a second miss within the week.
    assert streaks.streak(_days(0, 1, 3, 5, 6), TODAY)["current"] == 3
    assert streaks.streak(_days(0, 3, 4), TODAY)["current"] == 1


def test_rest_days_a_week_apart_are_both_forgiven():
    logged = _days(*[offset for offset in range(0, 16) if offset not in (2, 10)])
    assert streaks.streak(logged, TODAY)["current"] == 14


def test_nothing_logged_is_zero_and_best_remembers_an_old_run():
    assert streaks.streak(set(), TODAY)["current"] == 0
    old_run = _days(20, 21, 22, 23, 24)
    result = streaks.streak(old_run, TODAY)
    assert result["current"] == 0 and result["best"] == 5


def test_longest_run_is_strict():
    days = [date(2026, 10, d) for d in (1, 2, 3, 5, 6)]
    assert streaks.longest_run(days) == 3
    assert streaks.longest_run([]) == 0
