"""Achievement rules.

The rules are pure functions so they can be pinned precisely: a bug here either
withholds a badge the reader earned or hands out one they did not.
"""
from datetime import date, timedelta
import json

import pytest

from services.bible import achievements as ach


def day(offset: int, base: str = "2026-09-01") -> str:
    return (date.fromisoformat(base) + timedelta(days=offset)).isoformat()


def definition(rule_type: str, value: float, identifier: str = "x") -> ach.Achievement:
    return ach.Achievement(id=identifier, name="Test", description="", points=5, rule_type=rule_type, rule_value=value)


# ── definitions ─────────────────────────────────────────────────────────────


def test_shipped_definitions_are_valid():
    defs = ach.load_definitions()
    assert len(defs) >= 15
    ids = [d.id for d in defs]
    assert len(ids) == len(set(ids)), "duplicate achievement ids"
    for d in defs:
        assert d.name and d.points >= 0 and d.rule_type in ach.RULE_TYPES
        assert d.rule_value > 0


def test_every_rule_type_has_at_least_one_badge():
    used = {d.rule_type for d in ach.load_definitions()}
    assert used == set(ach.RULE_TYPES)


def test_a_bad_definition_file_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"kind": "wrong.kind", "achievements": []}))
    assert ach.load_definitions(path) == []


def test_unknown_rule_types_are_skipped(tmp_path):
    path = tmp_path / "mixed.json"
    path.write_text(
        json.dumps(
            {
                "kind": "jarvis.bible.achievements",
                "achievements": [
                    {"id": "ok", "name": "Fine", "points": 1, "rule": {"type": "chapters_total", "value": 5}},
                    {"id": "bad", "name": "Nope", "points": 1, "rule": {"type": "levitating", "value": 5}},
                ],
            }
        )
    )
    assert [d.id for d in ach.load_definitions(path)] == ["ok"]


def test_missing_file_yields_no_definitions(tmp_path):
    assert ach.load_definitions(tmp_path / "nope.json") == []


# ── streaks ─────────────────────────────────────────────────────────────────


def test_longest_streak_counts_consecutive_days():
    assert ach.longest_streak([day(0), day(1), day(2)]) == 3


def test_longest_streak_is_zero_without_days():
    assert ach.longest_streak([]) == 0


def test_longest_streak_breaks_on_a_gap():
    assert ach.longest_streak([day(0), day(1), day(5), day(6)]) == 2


def test_longest_streak_takes_the_best_run_not_the_last():
    days = [day(0), day(1), day(2), day(3), day(10)]
    assert ach.longest_streak(days) == 4


def test_current_streak_counts_through_yesterday():
    today = date(2026, 9, 3)
    assert ach.current_streak([day(0), day(1)], today=today) == 2


def test_current_streak_survives_an_unread_today():
    today = date(2026, 9, 3)
    assert ach.current_streak([day(0), day(1)], today=today) == 2


def test_current_streak_is_zero_after_a_missed_day():
    today = date(2026, 9, 5)
    assert ach.current_streak([day(0), day(1)], today=today) == 0


def test_current_streak_is_zero_when_only_the_future_is_recorded():
    today = date(2026, 9, 1)
    assert ach.current_streak([day(5)], today=today) == 0


def test_unparseable_days_are_ignored():
    assert ach.longest_streak([day(0), "not-a-day"]) == 1


# ── evaluate ────────────────────────────────────────────────────────────────


def test_a_met_rule_is_earned_today():
    defs = [definition("chapters_total", 3, "read3")]
    result = ach.evaluate(defs, {"chapters_total": 3.0}, today="2026-09-01")
    assert [e.achievement.id for e in result.earned] == ["read3"]
    assert result.earned[0].earned_on == "2026-09-01"
    assert result.points == 5


def test_an_unmet_rule_is_reported_as_next_up():
    defs = [definition("chapters_total", 10, "read10")]
    result = ach.evaluate(defs, {"chapters_total": 4.0}, today="2026-09-01")
    assert result.earned == []
    progress = result.next_up[0]
    assert progress.current == 4 and progress.target == 10
    assert progress.remaining == 6 and progress.percent == 40
    assert progress.unit == "chapters"


def test_a_banked_badge_keeps_its_original_date():
    defs = [definition("chapters_total", 3, "read3")]
    result = ach.evaluate(
        defs, {"chapters_total": 99.0}, previously_earned={"read3": "2026-01-01"}, today="2026-09-01"
    )
    assert result.earned[0].earned_on == "2026-01-01"


def test_missing_metrics_read_zero_rather_than_erroring():
    defs = [definition("quiz_perfect", 1, "ace")]
    result = ach.evaluate(defs, {}, today="2026-09-01")
    assert result.next_up[0].current == 0


def test_next_up_is_ranked_by_percent_not_raw_remaining():
    defs = [
        definition("chapters_total", 1000, "almost"),
        definition("quiz_perfect", 1, "one_more"),
    ]
    result = ach.evaluate(defs, {"chapters_total": 990.0, "quiz_perfect": 0.0}, today="2026-09-01")
    assert [p.achievement.id for p in result.next_up] == ["almost", "one_more"]


def test_percent_is_capped_below_one_hundred_when_unmet():
    defs = [definition("chapters_total", 4, "four")]
    result = ach.evaluate(defs, {"chapters_total": 3.0}, today="2026-09-01")
    assert result.next_up[0].percent == 75


def test_measure_ignores_junk_values():
    assert ach.measure("chapters_total", {"chapters_total": "not a number"}) == 0.0


# ── star conversion ─────────────────────────────────────────────────────────


def test_star_grants_use_the_achievement_reason():
    result = ach.evaluate([definition("chapters_total", 1, "one")], {"chapters_total": 5.0}, today="2026-09-01")
    assert ach.star_grants(result.earned) == [{"stars": 5, "reason": "achievement", "detail": "Test"}]


def test_star_grants_are_empty_when_nothing_was_earned():
    assert ach.star_grants([]) == []