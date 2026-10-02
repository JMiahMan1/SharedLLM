"""Metric history: workouts and distances, which behave unlike steps.

The point of these tests is the semantic split. Steps arrive as a daily sensor
reading, so "no reading" and "a reading of zero" are different facts. Workouts
are events someone recorded, so a day with none is a genuine zero -- and the
tests below hold that line, because getting it wrong is how a quiet month gets
rendered as a broken sensor.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from services.geo.metric_history import (
    AVAILABLE_METRICS,
    UNAVAILABLE_METRICS,
    build_metric_series,
    count_workouts_by_day,
    daily_from_trips,
    daily_from_workouts,
    metric_catalog,
    to_payload,
)

TZ = ZoneInfo("America/Phoenix")
TODAY = date(2025, 10, 1)


def iso(d: date) -> str:
    return d.isoformat()


def ts(d: date, hour: int = 12) -> float:
    """Epoch seconds for `d` at `hour` local time. datetime, not date --
    date.replace() has no `hour`."""
    return datetime(d.year, d.month, d.day, hour, tzinfo=TZ).timestamp()


class TestEmptyDaysAreRealZeros:
    """The whole reason this module is separate from step_history."""

    def test_a_day_with_no_workout_is_zero_not_missing(self):
        series = build_metric_series({}, TODAY, "W", "workouts")
        assert series.empty is True
        assert all(b.value == 0 for b in series.buckets)
        # No bucket claims to be "missing" -- there is no such thing here.
        assert all(b.active_days == 0 for b in series.buckets)

    def test_a_quiet_month_reports_zero_rather_than_no_data(self):
        series = build_metric_series({iso(TODAY - timedelta(days=2)): 1.0}, TODAY, "M", "workouts")
        assert series.total == 1.0
        quiet = [b for b in series.buckets if b.quiet]
        assert len(quiet) == 29
        # Zero days are counted as days, and excluded from the per-active average.
        assert series.active_days == 1
        assert series.per_active_day == 1.0

    def test_per_active_day_is_not_a_calendar_average(self):
        # 3 workouts on one day is 3.0 per active day, not 0.1 per calendar day.
        series = build_metric_series({iso(TODAY): 3.0}, TODAY, "M", "workouts")
        assert series.per_active_day == 3.0
        assert series.total == 3.0

    def test_empty_is_distinct_from_a_low_count(self):
        empty = build_metric_series({}, TODAY, "M", "workouts")
        low = build_metric_series({iso(TODAY): 1.0}, TODAY, "M", "workouts")
        assert empty.empty is True
        assert low.empty is False
        assert low.total == 1.0


class TestFoldingEvents:
    def test_counts_workouts_per_day(self):
        workouts = [{"start_time": ts(TODAY)}, {"start_time": ts(TODAY)}, {"start_time": ts(TODAY - timedelta(days=1))}]
        assert count_workouts_by_day(workouts, TZ) == {iso(TODAY): 2.0, iso(TODAY - timedelta(days=1)): 1.0}

    def test_sums_duration(self):
        workouts = [{"start_time": ts(TODAY), "duration_seconds": 1800}]
        daily = daily_from_workouts(workouts, TZ, fields={"workout_minutes": "duration_seconds"})
        # Keyed by metric, because one call can fold several at once.
        assert daily == {"workout_minutes": {iso(TODAY): 1800.0}}

    def test_folds_several_metrics_in_one_pass(self):
        workouts = [{"start_time": ts(TODAY), "duration_seconds": 600, "distance_miles": 1.25}]
        daily = daily_from_workouts(
            workouts, TZ, fields={"workout_minutes": "duration_seconds", "workout_miles": "distance_miles"}
        )
        assert daily["workout_minutes"][iso(TODAY)] == 600.0
        assert daily["workout_miles"][iso(TODAY)] == 1.25

    def test_sums_distance(self):
        trips = [{"start_time": ts(TODAY), "distance_miles": 2.5}, {"start_time": ts(TODAY), "distance_miles": 1.5}]
        assert daily_from_trips(trips, TZ) == {iso(TODAY): 4.0}

    def test_skips_events_with_no_timestamp(self):
        """An unplaceable event would otherwise inflate whichever day is queried."""
        workouts = [{"duration_seconds": 600}, {"start_time": ts(TODAY), "duration_seconds": 600}]
        daily = daily_from_workouts(workouts, TZ, fields={"workout_minutes": "duration_seconds"})
        assert daily == {"workout_minutes": {iso(TODAY): 600.0}}

    def test_ignores_zero_and_negative_values(self):
        workouts = [{"start_time": ts(TODAY), "distance_miles": 0}, {"start_time": ts(TODAY), "distance_miles": -3}]
        assert daily_from_trips(workouts, TZ) == {}

    def test_ignores_unparseable_values(self):
        workouts = [{"start_time": ts(TODAY), "distance_miles": "not a number"}]
        assert daily_from_trips(workouts, TZ) == {}

    def test_filing_uses_the_local_day_not_utc(self):
        # 23:30 local is the next UTC day; the bucket must follow the phone's
        # timezone, the same rule the steps fix established.
        late = datetime(2025, 10, 1, 23, 30, tzinfo=TZ).timestamp()
        assert list(daily_from_trips([{"start_time": late, "distance_miles": 1.0}], TZ)) == [iso(TODAY)]


class TestBuckets:
    def test_week_is_seven_daily_bars(self):
        series = build_metric_series({}, TODAY, "W", "workouts")
        assert len(series.buckets) == 7
        assert series.buckets[0].start == iso(TODAY - timedelta(days=6))
        assert series.buckets[-1].end == iso(TODAY)

    def test_month_is_thirty_daily_bars(self):
        assert len(build_metric_series({}, TODAY, "M", "workouts").buckets) == 30

    def test_year_is_twelve_monthly_bars(self):
        series = build_metric_series({}, TODAY, "Y", "workouts")
        assert len(series.buckets) == 12

    def test_three_months_is_weekly_and_ends_today(self):
        series = build_metric_series({}, TODAY, "3M", "workouts")
        assert len(series.buckets) == 13
        assert series.buckets[-1].end == iso(TODAY)

    def test_a_single_bucket_still_renders_one_bar(self):
        # Unlike the step chart (where one bar reads as a solid block), an
        # event metric with one bucket is legitimately "today".
        series = build_metric_series({iso(TODAY): 1.0}, TODAY, "D", "workouts")
        assert len(series.buckets) == 1
        assert series.buckets[0].value == 1.0

    def test_outside_the_window_is_excluded(self):
        old = {iso(TODAY - timedelta(days=400)): 5.0}
        series = build_metric_series(old, TODAY, "W", "workouts")
        assert series.total == 0.0

    def test_malformed_day_does_not_take_the_series_down(self):
        daily = {iso(TODAY): 2.0, "not-a-date": 99.0, "2025-13-45": 7.0}
        series = build_metric_series(daily, TODAY, "W", "workouts")
        assert series.total == 2.0

    def test_best_is_none_when_nothing_happened(self):
        assert build_metric_series({}, TODAY, "M", "workouts").best is None

    def test_best_points_at_the_busiest_bucket(self):
        daily = {iso(TODAY): 1.0, iso(TODAY - timedelta(days=5)): 4.0}
        series = build_metric_series(daily, TODAY, "M", "workouts")
        assert series.best.value == 4.0


class TestPayload:
    def test_reports_metric_and_range(self):
        payload = to_payload(build_metric_series({iso(TODAY): 2.0}, TODAY, "M", "workouts"))
        assert payload["metric"] == "workouts"
        assert payload["unit"] == "count"
        assert payload["range_label"] == "30 days"
        assert payload["total"] == 2.0

    def test_flags_quiet_buckets_so_the_ui_can_say_zero(self):
        payload = to_payload(build_metric_series({iso(TODAY): 1.0}, TODAY, "M", "workouts"))
        assert sum(1 for b in payload["buckets"] if b["quiet"]) == 29
        assert payload["empty"] is False

    def test_says_empty_explicitly(self):
        assert to_payload(build_metric_series({}, TODAY, "M", "workouts"))["empty"] is True

    def test_carries_the_format_the_ui_needs(self):
        payload = to_payload(build_metric_series({}, TODAY, "M", "workout_miles"))
        assert payload["format"] == "distance"
        assert payload["unit"] == "miles"


class TestHonestyAboutUntrackedMetrics:
    def test_calories_is_reported_as_untracked_with_a_reason(self):
        """The field is a hardcoded None; a card showing 0 kcal would be a lie."""
        assert "calories" in UNAVAILABLE_METRICS
        assert "None" in UNAVAILABLE_METRICS["calories"]

    def test_untracked_metrics_are_never_in_the_available_list(self):
        for name in UNAVAILABLE_METRICS:
            assert name not in AVAILABLE_METRICS

    def test_catalog_reports_both_halves(self):
        catalog = metric_catalog()
        assert "workouts" in catalog["available"]
        assert "calories" in catalog["unavailable"]

    def test_every_unavailable_metric_explains_itself(self):
        for name, reason in UNAVAILABLE_METRICS.items():
            assert reason and reason.strip(), f"{name} has no explanation"
            assert reason.endswith("."), f"{name} reason should read as a sentence"


class TestSharedCalendar:
    """The calendar is shared with step_history; these guard the seam."""

    @pytest.mark.parametrize("rng", ["D", "W", "M", "3M", "Y"])
    def test_every_range_buckets_without_raising(self, rng):
        assert build_metric_series({iso(TODAY): 1.0}, TODAY, rng, "workouts").buckets

    @pytest.mark.parametrize("rng", ["D", "W", "M", "3M", "Y"])
    def test_buckets_never_end_after_today(self, rng):
        for b in build_metric_series({}, TODAY, rng, "workouts").buckets:
            assert b.end <= iso(TODAY)

    def test_month_end_clamping_survives_the_seam(self):
        series = build_metric_series({}, date(2025, 3, 31), "Y", "workouts")
        # February 2026 has no 31st; the bucket must be shortened, not skipped.
        assert len(series.buckets) == 12
