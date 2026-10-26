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


def test_a_day_of_the_weekly_schedule_wins_over_daily_and_weekend():
    rules = {**RULES, "day_limits": {"wed": 60, "sat": 240}}
    assert evaluate(rules, 30 * 60000, 0, WED(16))["limit_min"] == 60
    assert evaluate(rules, 0, 0, SAT(12))["limit_min"] == 240
    assert evaluate({**RULES, "day_limits": {"mon": 30}}, 0, 0, WED(16))["limit_min"] == 120  # other days keep theirs


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


def test_a_parents_own_always_allowed_list_cannot_drop_the_safety_core():
    s = evaluate(
        {"always_allowed": ["com.example.reader"], "blocked_apps": ["com.google.android.dialer"]},
        used_ms=0,
        bonus_min=0,
        now=WED(16),
    )
    assert "com.example.reader" in s["always_allowed"]
    assert "com.google.android.dialer" in s["always_allowed"], "calling for help is never removed"
    assert "com.jarvisos.app" in s["always_allowed"], "nor is the lock screen's way to ask for more"
    assert s["blocked_apps"] == [], "a locked phone can still call even if the parent listed the dialer"


def test_a_parents_list_replaces_the_default_messaging_apps():
    s = evaluate({"always_allowed": ["com.example.reader"]}, used_ms=0, bonus_min=0, now=WED(16))
    assert "com.google.android.apps.messaging" not in s["always_allowed"], "the parent decides about texting"


def test_the_next_lock_is_named_ahead_for_the_one_minute_warning():
    before_bed = evaluate(RULES, used_ms=0, bonus_min=0, now=WED(20, 59))
    assert before_bed["next_lock_reason"] == "bedtime"
    assert before_bed["next_lock_at"] == "2026-10-21T21:00:00-07:00"
    # The limit runs out first when fewer minutes are left than until bedtime.
    nearly_out = evaluate(RULES, used_ms=119 * 60000, bonus_min=0, now=WED(16))
    assert (nearly_out["next_lock_reason"], nearly_out["next_lock_at"]) == ("limit", "2026-10-21T16:01:00-07:00")
    # School starts tomorrow morning only on a school day; Saturday looks past the weekend.
    school = {"school_start": "08:00", "school_end": "15:00"}
    after = evaluate(school, 0, 0, WED(16))
    assert (after["next_lock_reason"], after["next_lock_at"]) == ("school", "2026-10-22T08:00:00-07:00")
    assert evaluate(school, 0, 0, SAT(12))["next_lock_at"] is None


def test_no_next_lock_while_locked_or_without_rules():
    assert evaluate(RULES, 0, 0, WED(22))["next_lock_at"] is None
    assert evaluate({}, 0, 0, WED(12))["next_lock_reason"] is None


def test_install_approval_is_on_unless_a_parent_switched_it_off_and_gating_keeps_known_groups():
    assert evaluate({}, 0, 0, WED(12))["install_approval"] is True
    assert evaluate({"install_approval": False}, 0, 0, WED(12))["install_approval"] is False
    assert evaluate({"permission_gating": ["camera", "teleport"]}, 0, 0, WED(12))["permission_gating"] == ["camera"]


def test_a_site_allowed_for_a_while_counts_only_until_it_runs_out():
    wf = {
        "allow": ["khanacademy.org"],
        "allow_until": {"minecraft.net": "2026-10-21T17:00:00-07:00", "old.example": "2026-10-21T10:00:00-07:00"},
    }
    assert evaluate({"web_filter": wf}, 0, 0, WED(16))["web_allow"] == ["khanacademy.org", "minecraft.net"]
    assert evaluate({"web_filter": wf}, 0, 0, WED(18))["web_allow"] == ["khanacademy.org"]
