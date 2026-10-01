"""Range aggregation for step history.

The two things worth protecting are not the arithmetic -- they are the
distinctions that keep the chart honest: a day with no reading is not a day
with zero steps, and a baseline drawn from five days is not a baseline.
"""
from datetime import date, timedelta

import pytest

from services.geo.step_history import (
    BASELINE_MIN_DAYS,
    RANGE_BUCKETS,
    RANGE_DAYS,
    _add_months,
    build_buckets,
    build_series,
    personal_baseline,
    to_payload,
)

TODAY = date(2026, 10, 1)


def _by_days_ago(kw: dict) -> dict[int, int]:
    """{'d0': 100, 'd3': 200} -> {0: 100, 3: 200}.

    Tests read like the claim they make ("a day ago", "three days ago") rather
    than as three hand-checked dates.
    """
    return {int(k[1:]): v for k, v in kw.items()}


def history(**kw) -> dict[str, int]:
    """Wire format, as stored in Redis: ISO-string keys. For `build_series`."""
    return {
        (TODAY - timedelta(days=d)).isoformat(): v
        for d, v in _by_days_ago(kw).items()
    }


def days_map(**kw) -> dict[date, int]:
    """Parsed form, as `build_buckets` takes it internally."""
    return {TODAY - timedelta(days=d): v for d, v in _by_days_ago(kw).items()}


class TestAddMonths:
    def test_advances(self):
        assert _add_months(date(2026, 1, 15), 1) == date(2026, 2, 15)

    def test_rolls_the_year(self):
        assert _add_months(date(2026, 12, 10), 1) == date(2027, 1, 10)

    @pytest.mark.parametrize(
        "start,expected",
        [
            (date(2026, 1, 31), date(2026, 2, 28)),
            (date(2026, 3, 31), date(2026, 4, 30)),
            (date(2024, 1, 31), date(2024, 2, 29)),  # leap year
            (date(2023, 1, 31), date(2023, 2, 28)),  # not a leap year
        ],
    )
    def test_clamps_to_the_last_valid_day(self, start, expected):
        """Naive replace(month=...) raises here, and a silently skipped month is
        worse than a short one."""
        assert _add_months(start, 1) == expected

    def test_backwards_clamps_too(self):
        assert _add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)


class TestBuildBuckets:
    def test_day_is_one_bucket_for_today(self):
        b = build_buckets(days_map(d0=5000), TODAY, "D")
        assert len(b) == 1
        assert b[0].steps == 5000
        assert b[0].complete

    def test_day_with_no_reading_is_a_gap_not_a_zero(self):
        b = build_buckets(days_map(), TODAY, "D")
        assert b[0].steps == 0
        assert b[0].days_missing == 1
        assert not b[0].complete

    def test_week_is_seven_single_days(self):
        b = build_buckets(days_map(d0=100, d1=200), TODAY, "W")
        assert len(b) == RANGE_BUCKETS["W"]
        assert sum(x.steps for x in b) == 300

    def test_month_is_thirty_single_days(self):
        b = build_buckets(days_map(d0=1000), TODAY, "M")
        assert len(b) == RANGE_DAYS["M"]
        assert b[-1].steps == 1000

    def test_year_is_monthly_not_daily(self):
        """365 bars is unreadable on a phone and enormous over the wire."""
        b = build_buckets(days_map(d0=100), TODAY, "Y")
        assert len(b) == 12
        # Twelve calendar months ending in the current one, oldest first.
        assert b[-1].label == TODAY.strftime("%b")
        assert b[-1].end == TODAY.isoformat()

    def test_quarter_is_weekly(self):
        b = build_buckets(days_map(d0=100, d7=100), TODAY, "3M")
        assert 10 <= len(b) <= 14
        assert b[-1].steps == 100

    def test_week_buckets_start_on_monday(self):
        b = build_buckets(days_map(), TODAY, "3M")
        for bucket in b:
            # The first bucket may be a partial; the rest must be Mondays.
            if bucket.days_recorded or bucket.days_missing:
                start = date.fromisoformat(bucket.start)
                if start.weekday() == 0 or start < TODAY - timedelta(days=90):
                    break
        else:
            pytest.fail("no weekly bucket aligned to a Monday")


class TestGaps:
    def test_gaps_are_surfaced_not_hidden(self):
        """A broken sensor must not look like a sedentary week."""
        s = build_series(history(d0=5000, d1=5000), TODAY, "W")
        assert s.gaps is True
        assert sum(b.days_missing for b in s.buckets) > 0

    def test_a_complete_window_reports_no_gaps(self):
        s = build_series(history(**{f"d{i}": 100 for i in range(7)}), TODAY, "W")
        assert s.gaps is False
        assert all(b.complete for b in s.buckets)

    def test_days_missing_counts_every_absent_day(self):
        s = build_series({}, TODAY, "W")
        assert sum(b.days_missing for b in s.buckets) == 7
        assert s.total == 0


class TestPersonalBaseline:
    def test_median_ignores_a_single_huge_day(self):
        """One 30,000-step hike must not redefine 'normal'."""
        values = [8000] * 6 + [30000]
        assert personal_baseline(values) == 8000

    def test_median_of_an_even_count_averages_the_middle(self):
        assert personal_baseline([100, 200, 300, 400]) == 250

    def test_none_when_there_is_nothing(self):
        assert personal_baseline([]) is None

    def test_uses_the_users_own_history_not_the_visible_window(self):
        """A 7-day view should still be able to say how you usually do."""
        s = build_series(history(**{f"d{i}": 100 for i in range(30)}), TODAY, "W")
        assert s.baseline == 100
        assert s.baseline_days == 30


class TestHonesty:
    def test_too_little_history_says_so_instead_of_guessing(self):
        s = build_series(history(d0=9000, d1=9000), TODAY, "W")
        assert s.thin is True
        assert s.baseline is None
        assert not s.enough_for_baseline

    def test_enough_history_produces_a_baseline(self):
        s = build_series(
            history(**{f"d{i}": 5000 for i in range(BASELINE_MIN_DAYS)}), TODAY, "W"
        )
        assert s.thin is False
        assert s.baseline == 5000
        assert s.enough_for_baseline

    def test_malformed_buckets_do_not_take_down_the_history(self):
        raw = {TODAY.isoformat(): 5000, "not-a-date": 99, "2026-13-45": 1, "": 7}
        s = build_series(raw, TODAY, "D")
        assert s.total == 5000


class TestPayload:
    def test_states_its_requirements(self):
        p = to_payload(build_series({}, TODAY, "W"))
        assert p["thin"] is True
        assert p["baseline"] is None
        assert p["baseline_min_days"] == BASELINE_MIN_DAYS
        assert p["has_gaps"] is True

    def test_carries_the_goal_through(self):
        p = to_payload(build_series(history(d0=10), TODAY, "D", goal=10000), goal=10000)
        assert p["goal"] == 10000

    def test_reports_the_best_day(self):
        p = to_payload(build_series(history(d0=100, d1=9000), TODAY, "W"))
        assert p["best"]["steps"] == 9000

    def test_buckets_are_json_shaped(self):
        p = to_payload(build_series(history(d0=100), TODAY, "W"))
        for b in p["buckets"]:
            assert set(b) == {
                "label", "start", "end", "steps",
                "days_missing", "days_recorded", "complete",
            }
            assert isinstance(b["steps"], int)
