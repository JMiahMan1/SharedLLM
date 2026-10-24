from datetime import datetime
from zoneinfo import ZoneInfo

from services.common.screen_time import evaluate

TZ = ZoneInfo("America/Phoenix")
WED = lambda h, m=0: datetime(2026, 10, 21, h, m, tzinfo=TZ)  # noqa: E731 - a Wednesday
SAT = lambda h, m=0: datetime(2026, 10, 24, h, m, tzinfo=TZ)  # noqa: E731

RULES = {
    "daily_limit_min": 120,
    "weekend_limit_min": 180,
    "bedtime_start": "21:00",
    "bedtime_end": "07:00",
    "school_start": "08:00",
    "school_end": "15:00",
}


def test_time_left_counts_bonus_and_never_goes_negative():
    s = evaluate(RULES, used_ms=90 * 60000, bonus_min=15, now=WED(16))
    assert (s["limit_min"], s["used_min"], s["bonus_min"], s["remaining_min"]) == (120, 90, 15, 45)
    assert s["locked"] is False
    assert evaluate(RULES, 500 * 60000, 0, WED(16))["remaining_min"] == 0


def test_the_limit_locks_until_midnight():
    s = evaluate(RULES, used_ms=120 * 60000, bonus_min=0, now=WED(17))
    assert s["locked"] and s["reason"] == "limit"
    assert s["until"] == "2026-10-22T00:00:00-07:00"


def test_the_weekend_limit_replaces_the_daily_one():
    assert evaluate(RULES, 150 * 60000, 0, SAT(12))["remaining_min"] == 30


def test_bedtime_runs_overnight_and_wins_over_time_left():
    late = evaluate(RULES, 0, 0, WED(22, 30))
    assert late["locked"] and late["reason"] == "bedtime" and late["until"] == "2026-10-22T07:00:00-07:00"
    early = evaluate(RULES, 0, 0, WED(6, 15))
    assert early["reason"] == "bedtime" and early["until"] == "2026-10-21T07:00:00-07:00"
    assert evaluate(RULES, 0, 0, WED(7, 0))["locked"] is False


def test_school_time_is_weekdays_only():
    assert evaluate(RULES, 0, 0, WED(10))["reason"] == "school"
    assert evaluate(RULES, 0, 0, SAT(10))["locked"] is False


def test_a_pause_comes_first_and_ends_on_its_own():
    rules = {**RULES, "paused_until": "2026-10-21T16:30:00-07:00"}
    assert evaluate(rules, 0, 0, WED(16))["reason"] == "paused"
    assert evaluate(rules, 0, 0, WED(16, 45))["locked"] is False
    assert evaluate({"paused": True}, 0, 0, WED(16))["reason"] == "paused"


def test_no_rules_means_no_limit_and_no_lock():
    s = evaluate(None, 600 * 60000, 0, WED(23))
    assert s["limit_min"] is None and s["remaining_min"] is None and s["locked"] is False
    assert "com.jarvisos.app" in s["always_allowed"]


def test_a_blocked_app_is_blocked_all_day_but_the_phone_is_not():
    s = evaluate({"blocked_apps": ["com.roblox.client"]}, used_ms=0, bonus_min=0, now=WED(16))
    assert s["blocked_apps"] == ["com.roblox.client"]
    assert s["locked"] is False


def test_an_app_past_its_minutes_is_blocked_until_midnight():
    rules = {"app_limits": {"com.google.android.youtube": 30, "com.spotify.music": 60}}
    apps = {"com.google.android.youtube": 31 * 60000, "com.spotify.music": 20 * 60000}
    s = evaluate(rules, used_ms=51 * 60000, bonus_min=0, now=WED(16), apps_used_ms=apps)
    assert s["blocked_apps"] == ["com.google.android.youtube"]
    assert s["app_minutes_left"] == {"com.google.android.youtube": 0, "com.spotify.music": 40}


def test_always_allowed_apps_are_never_blocked():
    rules = {"blocked_apps": ["com.google.android.dialer"], "app_limits": {"com.jarvisos.app": 0}}
    s = evaluate(rules, used_ms=0, bonus_min=0, now=WED(16))
    assert s["blocked_apps"] == [] and s["app_minutes_left"] == {}
