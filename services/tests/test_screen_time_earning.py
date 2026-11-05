"""Earned screen time (docs/HEALTH_HUB.md, "Earning time and chores first"): steps, exercise
and parent-approved chores earn minutes, unused ones carry over until the week
ends, and "chores first" locks the phone until today's chores are ticked off."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from services.common.screen_time import carried_over, earned, evaluate, wants_progress, week_start

TZ = ZoneInfo("America/Phoenix")
WED = lambda h, m=0: datetime(2026, 10, 21, h, m, tzinfo=TZ)  # noqa: E731 - a Wednesday

EARN = {"steps_every": 1000, "steps_min": 5, "exercise_every": 15, "exercise_min": 10, "chore_min": 10, "max_min": 60}
RULES = {"daily_limit_min": 60, "earn": EARN}


def test_only_whole_blocks_earn_and_the_day_is_capped():
    got = earned(RULES, {"steps": 4999, "exercise_min": 29, "chores_done": 1})
    assert (got["parts"]["steps"]["earned_min"], got["parts"]["exercise"]["earned_min"], got["parts"]["chores"]["earned_min"]) == (20, 10, 10)
    assert got["total_min"] == 40
    lots = earned(RULES, {"steps": 20000, "chores_done": 5})
    assert (lots["uncapped_min"], lots["total_min"]) == (150, 60)


def test_a_source_that_could_not_be_read_earns_nothing():
    got = earned(RULES, {"steps": None, "exercise_min": None, "chores_done": None})
    assert got["total_min"] == 0 and got["parts"]["steps"]["count"] is None


def test_no_earning_rules_earn_nothing_and_ask_for_nothing():
    assert earned({"daily_limit_min": 60}, {"steps": 9000})["total_min"] == 0
    assert not wants_progress({"daily_limit_min": 60})
    assert wants_progress({"chores_first": True}) and wants_progress(RULES)
    # Half a rule (steps with no minutes) earns nothing rather than guessing.
    assert earned({"earn": {"steps_every": 1000}}, {"steps": 9000})["total_min"] == 0


def test_earned_minutes_extend_todays_limit():
    s = evaluate(RULES, 50 * 60000, 0, WED(16), progress={"steps": 3000, "chores_done": 1})
    assert (s["earned_min"], s["remaining_min"], s["locked"]) == (25, 35, False)


def test_unused_earned_minutes_carry_over_and_the_week_resets_on_monday():
    assert week_start(date(2026, 10, 21)) == date(2026, 10, 19)
    assert week_start(date(2026, 10, 19)) == date(2026, 10, 19)
    week = [
        # Monday: earned 30, used 10 past the limit: 20 left over.
        {"day": "2026-10-19", "used_min": 70, "bonus_min": 0, "progress": {"steps": 4000, "chores_done": 1}},
        # Tuesday: earned 10, and a parent's 15 bonus covered the first 15 over: 5 more spent.
        {"day": "2026-10-20", "used_min": 80, "bonus_min": 15, "progress": {"chores_done": 1}},
    ]
    assert carried_over(RULES, week) == 25
    s = evaluate(RULES, 60 * 60000, 0, WED(16), banked_min=25)
    assert (s["banked_min"], s["remaining_min"]) == (25, 25)


def test_a_day_with_no_limit_spends_none_of_the_bank():
    rules = {**RULES, "day_limits": {"mon": None}, "daily_limit_min": None, "weekend_limit_min": None}
    assert carried_over(rules, [{"day": "2026-10-19", "used_min": 500, "progress": {"chores_done": 2}}]) == 20


def test_earned_minutes_never_go_below_nothing():
    week = [{"day": "2026-10-19", "used_min": 300, "progress": {"chores_done": 1}}]
    assert carried_over(RULES, week) == 0


def test_chores_first_locks_until_the_last_chore_is_ticked_off():
    rules = {**RULES, "chores_first": True}
    s = evaluate(rules, 0, 0, WED(16), progress={"chores_left": 2, "chores_done": 0})
    assert (s["locked"], s["reason"], s["until"], s["chores_left"]) == (True, "chores", None, 2)
    assert evaluate(rules, 0, 0, WED(16), progress={"chores_left": 0})["locked"] is False


def test_chores_first_does_not_lock_when_the_chart_cannot_be_read():
    assert evaluate({"chores_first": True}, 0, 0, WED(16), progress={"chores_left": None})["locked"] is False
    assert evaluate({"chores_first": True}, 0, 0, WED(16))["locked"] is False


def test_time_a_parent_gives_opens_a_chores_lock_and_it_returns_after():
    rules = {"chores_first": True}
    s = evaluate(rules, 0, 15, WED(16), granted_until=WED(16, 15), progress={"chores_left": 1})
    assert s["locked"] is False
    assert (s["next_lock_reason"], s["next_lock_at"]) == ("chores", "2026-10-21T16:15:00-07:00")
    assert evaluate(rules, 0, 15, WED(16, 16), granted_until=WED(16, 15), progress={"chores_left": 1})["reason"] == "chores"


def test_bedtime_school_and_a_pause_come_before_chores():
    rules = {"chores_first": True, "bedtime_start": "21:00", "bedtime_end": "07:00", "school_start": "08:00", "school_end": "15:00"}
    left = {"chores_left": 1}
    assert evaluate(rules, 0, 0, WED(22), progress=left)["reason"] == "bedtime"
    assert evaluate(rules, 0, 0, WED(9), progress=left)["reason"] == "school"
    assert evaluate({**rules, "paused": True}, 0, 0, WED(16), progress=left)["reason"] == "paused"


def test_chores_lock_comes_before_the_limit_so_the_reason_says_what_to_do():
    rules = {**RULES, "chores_first": True}
    assert evaluate(rules, 120 * 60000, 0, WED(16), progress={"chores_left": 1})["reason"] == "chores"
