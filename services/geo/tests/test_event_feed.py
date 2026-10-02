"""The health event timeline: ordering, deduping, and the honesty rules.

Pure module, so no Redis and no server. Each test names the failure it prevents
rather than the function it calls.
"""
from datetime import datetime, timezone

import pytest

from services.geo.event_feed import (
    Event,
    build_timeline,
    dedupe,
    events_from_achievements,
    events_from_trips,
    events_from_workouts,
    relative_day_label,
    timeline_payload,
)

TZ = timezone.utc
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=TZ).timestamp()


def at(day: str, hour: int = 12, minute: int = 0) -> float:
    return datetime.strptime(f"{day} {hour:02d}:{minute:02d}:00", "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=TZ
    ).timestamp()


class TestWorkoutEvents:
    def test_builds_a_titled_event(self):
        events = events_from_workouts(
            [{"activity_type": "running", "start_time": at("2026-09-30", 7)}], TZ
        )
        assert len(events) == 1
        assert events[0].kind == "workout"
        assert events[0].title == "Run"

    def test_uses_the_readable_label_not_the_raw_type(self):
        events = events_from_workouts(
            [{"activity_type": "mountain_biking", "start_time": at("2026-09-30")}], TZ
        )
        assert events[0].title == "Mountain Bike"

    def test_falls_back_to_a_readable_title_for_an_unknown_type(self):
        events = events_from_workouts(
            [{"activity_type": "wheelbarrow_racing", "start_time": at("2026-09-30")}], TZ
        )
        assert events[0].title == "Wheelbarrow Racing"

    def test_includes_duration_and_distance_when_present(self):
        events = events_from_workouts(
            [
                {
                    "activity_type": "cycling",
                    "start_time": at("2026-09-30"),
                    "duration_seconds": 3600,
                    "distance_miles": 18.5,
                }
            ],
            TZ,
        )
        assert "1h" in events[0].detail
        assert "18.5 mi" in events[0].detail

    def test_formats_a_duration_in_days_when_it_is_that_long(self):
        events = events_from_workouts(
            [{"activity_type": "walking", "start_time": at("2026-09-30"), "duration_seconds": 200000}],
            TZ,
        )
        assert "2d" in events[0].detail

    def test_omits_a_zero_distance_rather_than_printing_zero(self):
        events = events_from_workouts(
            [{"activity_type": "walking", "start_time": at("2026-09-30"), "distance_miles": 0}], TZ
        )
        assert "mi" not in events[0].detail

    # The rule that matters: dating an undated event "now" would float it to
    # the top of the timeline and read as the most recent thing you did.
    @pytest.mark.parametrize("bad", [None, "", "not-a-time", float("nan"), float("inf"), True])
    def test_drops_an_undated_workout_rather_than_dating_it_now(self, bad):
        assert events_from_workouts([{"activity_type": "run", "start_time": bad}], TZ) == []


class TestTripEvents:
    def test_builds_a_drive_event(self):
        events = events_from_trips(
            [{"activity_type": "driving", "start_time": at("2026-09-30"), "distance_miles": 12}], TZ
        )
        assert events[0].kind == "drive"
        assert events[0].title == "Drive"

    def test_assumes_driving_when_the_field_is_absent(self):
        events = events_from_trips([{"start_time": at("2026-09-30")}], TZ)
        assert len(events) == 1

    # Trips are also used for non-driving activity, which does not belong on a
    # health timeline under the name "Drive".
    def test_ignores_a_trip_that_is_not_driving(self):
        assert events_from_trips([{"activity_type": "walking", "start_time": at("2026-09-30")}], TZ) == []

    def test_drops_an_undated_trip(self):
        assert events_from_trips([{"activity_type": "driving"}], TZ) == []


class TestAchievementEvents:
    def test_parses_a_date_only_ledger_value(self):
        events = events_from_achievements({"first_100k": "2026-09-30"}, {})
        assert len(events) == 1
        assert events[0].kind == "achievement"

    def test_uses_the_definition_name_when_available(self):
        class Meta:
            name = "First 100k steps"
            description = "Walked 100,000 steps"

        events = events_from_achievements({"first_100k": "2026-09-30"}, {"first_100k": Meta})
        assert events[0].title == "Earned First 100k steps"
        assert events[0].detail == "Walked 100,000 steps"

    def test_survives_a_definition_that_is_a_plain_dict(self):
        events = events_from_achievements({"a": "2026-09-30"}, {"a": {"name": "Streaker"}})
        assert events[0].title == "Earned Streaker"

    def test_still_reports_the_badge_when_the_name_is_unknown(self):
        events = events_from_achievements({"mystery": "2026-09-30"}, {})
        assert events[0].title == "Achievement earned"

    # Dated at end of day so a badge earned today lands after a morning
    # workout rather than before it.
    def test_dates_a_date_only_value_at_the_end_of_that_day(self):
        events = events_from_achievements({"a": "2026-09-30"}, {})
        got = datetime.fromtimestamp(events[0].at, TZ)
        assert (got.hour, got.minute) == (23, 59)

    def test_keeps_the_exact_time_when_the_value_carries_one(self):
        events = events_from_achievements({"a": "2026-09-30T08:30:00"}, {})
        got = datetime.fromtimestamp(events[0].at, TZ)
        assert (got.hour, got.minute) == (8, 30)

    def test_accepts_a_raw_epoch(self):
        events = events_from_achievements({"a": at("2026-09-30", 9)}, {})
        assert datetime.fromtimestamp(events[0].at, TZ).hour == 9

    @pytest.mark.parametrize("bad", [None, "", "sometime last week", float("nan"), True])
    def test_drops_a_badge_it_cannot_place(self, bad):
        assert events_from_achievements({"a": bad}, {}) == []


class TestDedupe:
    def test_collapses_the_same_event_from_two_sources(self):
        t = at("2026-09-30", 9)
        once = [Event("workout", t, "Run"), Event("workout", t, "Run")]
        assert len(dedupe(once)) == 1

    def test_keeps_two_genuine_back_to_back_workouts(self):
        keep = [Event("workout", at("2026-09-30", 9), "Run"), Event("workout", at("2026-09-30", 10), "Run")]
        assert len(dedupe(keep)) == 2

    def test_does_not_merge_different_kinds_at_the_same_time(self):
        both = [Event("workout", at("2026-09-30"), "Run"), Event("drive", at("2026-09-30"), "Drive")]
        assert len(dedupe(both)) == 2

    def test_tolerates_sub_second_difference_for_the_same_event(self):
        t = at("2026-09-30", 9)
        assert len(dedupe([Event("workout", t, "Run"), Event("workout", t + 0.4, "Run")])) == 1


class TestRelativeLabels:
    def test_labels_today_and_yesterday(self):
        assert relative_day_label("2026-10-01", NOW, TZ) == "Today"
        assert relative_day_label("2026-09-30", NOW, TZ) == "Yesterday"

    def test_counts_days_up_to_a_week(self):
        assert relative_day_label("2026-09-26", NOW, TZ) == "5 days ago"

    def test_falls_back_to_a_date_beyond_a_week(self):
        # "12 days ago" stops being useful; an absolute date is more honest.
        assert relative_day_label("2026-09-15", NOW, TZ) != "18 days ago"
        assert "Sep" in relative_day_label("2026-09-15", NOW, TZ)

    def test_names_a_future_date_rather_than_burying_it_under_today(self):
        assert relative_day_label("2026-10-05", NOW, TZ) == "Future-dated"

    def test_echoes_an_unparseable_day_unchanged(self):
        assert relative_day_label("garbage", NOW, TZ) == "garbage"


class TestBuildTimeline:
    def test_sorts_newest_first_regardless_of_input_order(self):
        events = [
            Event("workout", at("2026-09-25"), "Old"),
            Event("workout", at("2026-09-30"), "New"),
            Event("workout", at("2026-09-28"), "Middle"),
        ]
        groups = build_timeline(events, NOW, TZ)
        titles = [e.title for g in groups for e in g.events]
        assert titles == ["New", "Middle", "Old"]

    def test_groups_by_calendar_day(self):
        events = [
            Event("workout", at("2026-09-30", 8), "Morning"),
            Event("workout", at("2026-09-30", 20), "Evening"),
        ]
        groups = build_timeline(events, NOW, TZ)
        assert len(groups) == 1
        assert groups[0].day == "2026-09-30"
        assert len(groups[0].events) == 2

    def test_labels_each_group(self):
        events = [Event("workout", at("2026-10-01", 8), "This morning")]
        assert build_timeline(events, NOW, TZ)[0].relative_label == "Today"

    def test_attaches_days_ago_against_the_supplied_now(self):
        events = [Event("workout", at("2026-09-27"), "Three days back")]
        got = build_timeline(events, NOW, TZ)[0].events[0]
        assert got.days_ago == 4

    def test_never_reports_a_negative_days_ago(self):
        # A clock skew upstream must not render as "-3 days ago".
        events = [Event("workout", NOW + 3 * 86400, "From the future")]
        assert build_timeline(events, NOW, TZ)[0].events[0].days_ago == 0

    def test_attaches_a_time_label_in_the_bucketing_zone(self):
        # The client must not format `at` itself: on a device in another zone
        # it would file the event on a different day than the group it is
        # listed under.
        events = [Event("workout", at("2026-10-01", 8), "This morning")]
        got = build_timeline(events, NOW, TZ)[0].events[0]
        assert got.time_label.endswith("AM") or got.time_label.endswith("PM")
        assert ":" in got.time_label

    def test_drops_an_epoch_it_cannot_place_on_a_day(self):
        # A wildly out-of-range epoch must not take down the whole timeline,
        # and must not be filed under a made-up day either.
        events = [
            Event("workout", 1e18, "Broken clock"),
            Event("workout", at("2026-10-01", 8), "This morning"),
        ]
        groups = build_timeline(events, NOW, TZ)
        assert len(groups) == 1
        assert [e.title for e in groups[0].events] == ["This morning"]

    def test_time_label_is_blank_when_never_bucketed(self):
        # Defence in depth: to_dict can be called on an event that never went
        # through build_timeline.
        assert Event("workout", at("2026-10-01", 8), "x").to_dict()["time_label"] == ""

    def test_serialises_both_labels(self):
        events = [Event("workout", at("2026-10-01", 8), "This morning")]
        payload = build_timeline(events, NOW, TZ)[0].events[0].to_dict()
        assert payload["label"] == "Workout"  # the kind's name
        assert payload["time_label"] != ""  # the time of day

    def test_caps_the_total_across_days(self):
        events = [Event("workout", at("2026-09-30", h), f"w{h}") for h in range(20)]
        groups = build_timeline(events, NOW, TZ, limit=5)
        assert sum(len(g.events) for g in groups) == 5

    def test_an_empty_timeline_yields_no_groups(self):
        assert build_timeline([], NOW, TZ) == []


class TestPayload:
    def test_reports_empty_rather_than_a_bare_list(self):
        payload = timeline_payload(build_timeline([], NOW, TZ), NOW)
        assert payload["empty"] is True
        assert payload["total_events"] == 0
        assert payload["groups"] == []

    def test_counts_events_and_days(self):
        events = [
            Event("workout", at("2026-09-30", 8), "A"),
            Event("workout", at("2026-09-30", 20), "B"),
            Event("workout", at("2026-09-29"), "C"),
        ]
        payload = timeline_payload(build_timeline(events, NOW, TZ), NOW)
        assert payload["empty"] is False
        assert payload["total_events"] == 3
        # day_count, not days: the route merges the requested window alongside
        # this, and a shared key silently lost the window.
        assert payload["day_count"] == 2
        assert "days" not in payload

    def test_each_event_carries_a_label_and_icon(self):
        events = [Event("achievement", at("2026-09-30"), "Badge")]
        payload = timeline_payload(build_timeline(events, NOW, TZ), NOW)
        ev = payload["groups"][0]["events"][0]
        assert ev["label"] == "Achievement"
        assert ev["icon"]

    def test_a_group_serialises_its_relative_label(self):
        events = [Event("workout", at("2026-10-01"), "X")]
        payload = timeline_payload(build_timeline(events, NOW, TZ), NOW)
        assert payload["groups"][0]["relative"] == "Today"
