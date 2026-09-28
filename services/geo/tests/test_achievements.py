"""Goals and achievements tests.

The rules are pure functions so they can be pinned precisely: a bug here would
either withhold a badge the user earned or hand out one they did not.
"""


from datetime import date, timedelta

import pytest

from services.geo import achievements as ach


def day(offset: int, base: str = "2026-09-01") -> str:
    return (date.fromisoformat(base) + timedelta(days=offset)).isoformat()


class FakeRedis:
    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def hset(self, key, mapping=None, **kwargs):
        bucket = self.hashes.setdefault(key, {})
        for k, v in (mapping or {}).items():
            bucket[k] = str(v)
        return len(mapping or {})


def definitions() -> list[ach.Achievement]:
    return ach.load_definitions()


# ── definitions ─────────────────────────────────────────────────────────────

def test_shipped_definitions_are_valid():
    defs = definitions()
    assert len(defs) >= 15
    ids = [d.id for d in defs]
    assert len(ids) == len(set(ids)), "duplicate achievement ids"
    for d in defs:
        assert d.name and d.points >= 0 and d.rule_type in ach.RULE_TYPES


def test_invalid_definitions_are_skipped_not_fatal(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(
        '{"schemaVersion": 1, "kind": "jarvis.achievements", "achievements": ['
        '{"id": "ok", "name": "Fine", "points": 1, "rule": {"type": "steps_any", "value": 1}},'
        '{"id": "nope", "name": "Bad rule", "points": 5, "rule": {"type": "teleport", "value": 1}}'
        "]}"
    )
    defs = ach.load_definitions(bad)
    assert [d.id for d in defs] == ["ok"]


# ── metrics ─────────────────────────────────────────────────────────────────

def test_longest_streak_counts_consecutive_days_only():
    steps = {
        day(0): 11000,
        day(1): 12000,
        day(2): 9000,   # breaks the run
        day(3): 10500,
        day(4): 10000,
        day(5): 10000,
    }
    assert ach.longest_goal_streak(steps, 10000) == 3
    # The gap day splits the two runs (2 before, 3 after)
    assert ach.goal_dates(steps, 10000) == [date.fromisoformat(day(i)) for i in (0, 1, 3, 4, 5)]


def test_streak_with_no_goal_days_is_zero():
    assert ach.longest_goal_streak({day(0): 100, day(1): 200}, 10000) == 0


def test_weekly_total_uses_the_best_iso_week():
    # ISO weeks run Mon-Sun: 09-07 is a Monday, so the second week is
    # 09-07..09-13 = 10,000 (09-07) + 12,000 * 6 = 82,000.
    week_one = {day(i): 10000 for i in range(7)}          # 09-01..09-07
    week_two = {day(i + 7): 12000 for i in range(7)}      # 09-08..09-14
    assert ach.max_steps_in_week({**week_one, **week_two}) == 82000


def test_goal_after_gap_requires_a_real_run_of_quiet_days():
    quiet_then_goal = {
        day(0): 1000,
        day(1): 1000,
        day(2): 1000,
        day(3): 12000,
    }
    assert ach.has_goal_after_gap(quiet_then_goal, 10000, 3) is True

    two_quiet = {day(0): 1000, day(1): 1000, day(2): 12000}
    assert ach.has_goal_after_gap(two_quiet, 10000, 3) is False


def test_missing_days_do_not_fabricate_a_comeback():
    # Only two recorded days, neither quiet, no gap: must be False
    sparse = {day(0): 12000, day(5): 12000}
    assert ach.has_goal_after_gap(sparse, 10000, 3) is False


def test_workouts_in_best_week_and_total_miles():
    base = 1_756_000_000  # arbitrary; only relative grouping matters
    workouts = [
        {"start_time": base, "distance_miles": 2.5},
        {"start_time": base + 3600, "distance_miles": 1.5},
        {"start_time": base + 86400 * 8, "distance_miles": 4.0},
    ]
    assert ach.workouts_in_best_week(workouts) >= 2
    assert ach.total_workout_miles(workouts) == 8.0
    assert ach.total_workout_miles([{"distance_miles": None}]) == 0.0


# ── evaluation ──────────────────────────────────────────────────────────────

def test_evaluate_unlocks_thresholds_and_reports_next_up():
    defs = definitions()
    steps = {day(i): 10500 for i in range(3)}
    result = ach.evaluate(
        defs, steps, [], ach.DEFAULT_GOALS, previously_earned={}, today=day(3)
    )
    earned = {e.achievement.id for e in result.earned}
    assert {"first_steps", "steps_5k_day", "steps_10k_day", "goal_1", "goal_streak_3"} <= earned
    # 7-day streak is not earned yet but must be the closest next-up
    next_up = {p.achievement.id: p for p in result.next_up}
    assert "goal_streak_7" in next_up
    assert next_up["goal_streak_7"].current == 3
    assert next_up["goal_streak_7"].percent == 43  # 3/7 days, rounded


def test_evaluate_keeps_already_earned_dates_stable():
    defs = definitions()
    ledger = {"first_steps": "2026-01-01", "steps_10k_day": "2026-02-02"}
    result = ach.evaluate(
        defs, {day(0): 11000}, [], ach.DEFAULT_GOALS, previously_earned=ledger, today=day(1)
    )
    earned = {e.achievement.id: e.earned_on for e in result.earned}
    assert earned["first_steps"] == "2026-01-01"
    assert earned["steps_10k_day"] == "2026-02-02"
    # A badge is never reported twice
    assert [e.achievement.id for e in result.earned].count("first_steps") == 1


def test_evaluate_never_grants_badges_without_data():
    defs = definitions()
    result = ach.evaluate(defs, {}, [], ach.DEFAULT_GOALS, previously_earned={})
    assert result.earned == []
    assert result.points == 0


def test_points_total_ignores_unknown_ledger_keys():
    defs = definitions()
    by_id = {d.id: d for d in defs}
    ledger = {"first_steps": "2026-01-01", "ghost_badge": "2026-01-01"}
    expected = by_id["first_steps"].points
    assert ach.total_points(ledger, defs) == expected


# ── goals ───────────────────────────────────────────────────────────────────

def test_clean_goals_defaults_and_bounds():
    assert ach.clean_goals(None) == ach.DEFAULT_GOALS
    # Out-of-range and unknown keys are ignored, valid ones applied
    goals = ach.clean_goals({"daily_steps": "15000", "weekly_steps": 1, "nonsense": 5})
    assert goals["daily_steps"] == 15000
    assert goals["weekly_steps"] == ach.DEFAULT_GOALS["weekly_steps"]


async def test_goals_round_trip_and_daily_goal_sync():
    rc = FakeRedis()
    saved = await ach.save_goals(rc, "jeremiah", {"daily_steps": 12000, "workouts_per_week": 5})
    assert saved["daily_steps"] == 12000
    assert saved["workouts_per_week"] == 5

    loaded = await ach.load_goals(rc, "jeremiah")
    assert loaded == saved


async def test_record_awards_is_idempotent():
    rc = FakeRedis()
    defs = definitions()
    first = ach.evaluate(defs, {day(0): 11000}, [], ach.DEFAULT_GOALS, previously_earned={}, today=day(0))
    await ach.record_awards(rc, "jeremiah", first.earned)
    ledger_after_first = await ach.load_points_ledger(rc, "jeremiah")

    second = ach.evaluate(
        defs, {day(0): 11000}, [], ach.DEFAULT_GOALS,
        previously_earned=ledger_after_first, today=day(1),
    )
    newly = [e for e in second.earned if e.achievement.id not in ledger_after_first]
    await ach.record_awards(rc, "jeremiah", newly)
    assert await ach.load_points_ledger(rc, "jeremiah") == ledger_after_first

def test_next_up_is_ranked_by_completion_not_raw_units():
    """1,360 steps to go must rank above 1 workout to go.

    Raw `remaining` mixes steps, days and workouts; ranking by percent is
    unit-free and is what the UI shows.
    """
    defs = definitions()
    # 8,640 steps = 86% of the 10k badge; no workouts at all
    result = ach.evaluate(
        defs, {day(0): 8640}, [], ach.DEFAULT_GOALS, previously_earned={}, today=day(0)
    )
    ids = [p.achievement.id for p in result.next_up]
    assert ids.index("steps_10k_day") < ids.index("workout_1")
    assert result.next_up[0].achievement.id == "steps_10k_day"
