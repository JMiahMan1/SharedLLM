"""The chat coach answers first-person health questions from the person's own data."""
import pytest

from services.common import health_coach as coach

ON = {key: True for key in ("readiness", "checkin", "sleep", "nutrition", "water", "steps", "weekly_digest", "coach")}


def _summary(**extra):
    base = {
        "profile": {"kid_mode": False, "metrics": dict(ON)},
        "sleep": {
            "last_night": {"duration_minutes": 380},
            "score": {"score": 71, "label": "fair"},
            "need_minutes": 480,
        },
        "nutrition": {
            "logged": True,
            "entries_count": 3,
            "totals": {"calories": 1450, "protein_g": 62},
            "target": {"min": 1900, "max": 2100, "protein_g": 110},
        },
        "water": {"logged": True, "ml": 1200, "goal_ml": 2000},
        "readiness": {"score": 64, "label": "steady", "detail": "A normal day fits; go easy on hard training."},
    }
    base.update(extra)
    return base


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("How did I sleep?", "sleep"),
        ("how much protein is left today", "food"),
        ("calories left?", "food"),
        ("How much water have I had", "water"),
        ("what's my readiness", "readiness"),
        ("How was my week?", "week"),
        ("how are my goals going", "goals"),
        ("How was Emma's week?", None),
        ("tell me a joke", None),
        ("how did the game go", None),
    ],
)
def test_topics(query, expected):
    assert coach.topic(query) == expected


def test_sleep_answer_names_the_gap_and_week():
    text = coach.answer("sleep", _summary(), {"averages": {"sleep_minutes": 425}}, None)
    assert "6 h 20 min" in text and "1 h 40 min under" in text and "7 h 5 min" in text


def test_food_answer_for_an_adult_and_a_child():
    adult = coach.answer("food", _summary(), None, None)
    assert "1,450 kcal" in adult and "48 g of protein to go" in adult
    child = coach.answer("food", _summary(profile={"kid_mode": True, "metrics": dict(ON)}), None, None)
    assert "kcal" not in child and "3 things" in child


def test_nothing_logged_says_so_instead_of_guessing():
    empty = _summary(nutrition={"logged": False}, sleep={"last_night": None, "need_minutes": 480}, readiness=None)
    assert "Nothing is logged" in coach.answer("food", empty, None, None)
    assert "no sleep logged" in coach.answer("sleep", empty, None, None)
    assert "no readiness score" in coach.answer("readiness", empty, None, None)


def test_water_and_goals():
    assert "800 ml to go" in coach.answer("water", _summary(), None, None)
    goals = {"goals": [{"title": "Water goal days", "count": 2, "target_days": 4, "unit": "days", "reached": False}]}
    assert "Water goal days: 2 of 4 days" in coach.answer("goals", _summary(), None, goals)
    suggestion = {"goals": [], "suggestion": {"title": "Cook nights", "target_days": 2, "unit": "nights", "reason": "Last week you managed 1."}}
    assert "cook nights, 2 nights" in coach.answer("goals", _summary(), None, suggestion)


def test_switched_off_topics_and_coach_fall_through():
    summary = _summary()
    assert coach.allowed("sleep", summary)
    summary["profile"]["metrics"]["sleep"] = False
    assert not coach.allowed("sleep", summary)
    summary["profile"]["metrics"]["coach"] = False
    assert not coach.allowed("water", summary)
