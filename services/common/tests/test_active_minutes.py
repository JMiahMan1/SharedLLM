"""Active-Zone-like minutes from steps, workouts, heart rate and device totals."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from services.common import active_minutes as am

TZ = ZoneInfo("America/Chicago")
WED = date(2026, 10, 21)  # Monday is the 19th


def _run(**kw):
    base = {
        "today": WED,
        "tz": TZ,
        "hourly_steps": None,
        "workouts": None,
        "heart_rate": None,
        "device_minutes": None,
        "age": 40,
        "target_minutes": 150,
    }
    return am.compute(**{**base, **kw})


def _day(result, day):
    return next(entry for entry in result["days"] if entry["day"] == day)


def test_step_minutes_count_brisk_steps_beyond_a_baseline():
    assert am.step_minutes(900) == 0
    assert am.step_minutes(1500) == 5
    assert am.step_minutes(4000) == 30
    assert am.step_minutes(12000) == 60


def test_nothing_recorded_is_missing_not_zero():
    result = _run()
    assert result["total_minutes"] is None
    assert all(entry["minutes"] is None for entry in result["days"])
    assert result["percent"] is None
    assert result["notes"]


def test_overlapping_devices_take_the_highest_not_the_sum():
    steps = {"phone": {"2026-10-20:07": 4000}, "watch": {"2026-10-20:07": 3500}}
    result = _run(hourly_steps=steps)
    assert _day(result, "2026-10-20")["minutes"] == 30
    assert result["estimate"] is True
    assert result["percent"] == 20


def test_one_walk_seen_three_ways_counts_once():
    start = datetime(2026, 10, 20, 7, 0, tzinfo=TZ).timestamp()
    workouts = [{"start_time": start, "duration_seconds": 40 * 60, "status": "completed"}]
    steps = {"phone": {"2026-10-20:07": 4000}}
    result = _run(hourly_steps=steps, workouts=workouts)
    assert _day(result, "2026-10-20") == {"day": "2026-10-20", "minutes": 40, "sources": ["workouts"]}


def test_a_workout_across_the_hour_is_split():
    start = datetime(2026, 10, 19, 18, 30, tzinfo=TZ).timestamp()
    result = _run(workouts=[{"start_time": start, "end_time": start + 3600, "status": "completed"}])
    assert _day(result, "2026-10-19")["minutes"] == 60


def test_heart_rate_zones_count_vigorous_double_and_need_an_age():
    # Max 180 at 40: moderate from 115.2, vigorous from 138.6.
    samples = [
        {"recorded_at": "2026-10-21T06:00:10-05:00", "value": 120},
        {"recorded_at": "2026-10-21T06:00:40-05:00", "value": 118},  # same minute: once
        {"recorded_at": "2026-10-21T06:01:00-05:00", "value": 150},
        {"recorded_at": "2026-10-21T06:02:00-05:00", "value": 90},
    ]
    assert _day(_run(heart_rate=samples), "2026-10-21")["minutes"] == 3
    no_age = _run(heart_rate=samples, age=None)
    assert _day(no_age, "2026-10-21")["minutes"] is None
    assert any("birth year" in note for note in no_age["notes"])


def test_a_device_total_wins_its_day():
    steps = {"phone": {"2026-10-21:07": 8000}}
    device = [{"recorded_at": "2026-10-21T20:00:00-05:00", "value": 22}]
    result = _run(hourly_steps=steps, device_minutes=device)
    assert _day(result, "2026-10-21") == {"day": "2026-10-21", "minutes": 22, "sources": ["device"]}


def test_future_days_and_last_week_are_left_out():
    steps = {"phone": {"2026-10-18:07": 9000, "2026-10-23:07": 9000}}
    result = _run(hourly_steps=steps)
    assert result["week_start"] == "2026-10-19" and result["week_end"] == "2026-10-25"
    assert result["total_minutes"] is None


def test_no_target_means_no_percent():
    assert _run(hourly_steps={"phone": {"2026-10-20:07": 4000}}, target_minutes=None)["percent"] is None
