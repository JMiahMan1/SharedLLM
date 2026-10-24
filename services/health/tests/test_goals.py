"""Gentle weekly goals: two at most, a small step up suggested from last week."""
from __future__ import annotations

from datetime import date, timedelta

from services.health import goals

MONDAY = date(2026, 10, 19)


def test_the_suggestion_is_one_more_than_last_week_for_the_kind_with_most_room():
    pick = goals.suggestion({"water_days": 3, "sleep_nights": 5, "cook_nights": 1}, set())
    assert pick["kind"] == "cook_nights" and pick["target_days"] == 2
    assert "One more" in pick["reason"]
    assert goals.suggestion({"water_days": 0, "sleep_nights": 0, "cook_nights": 0}, set())["target_days"] == 1


def test_no_suggestion_when_everything_is_taken_or_full():
    assert goals.suggestion({"water_days": 7, "sleep_nights": 7, "cook_nights": 2}, {"cook_nights"}) is None


def test_set_a_goal_and_watch_it_count(client):
    client.put("/profile", json={"water_goal_ml": 1500})
    body = client.post("/goals", json={"kind": "water_days", "day": MONDAY.isoformat(), "target_days": 2}).json()
    assert body["week_start"] == MONDAY.isoformat()
    for offset in range(2):
        day = (MONDAY + timedelta(days=offset)).isoformat()
        client.post("/metrics", json={"samples": [{"metric": "water", "value": 1600, "unit": "ml", "recorded_at": f"{day}T12:00:00"}]})
    goal = client.get(f"/goals?day={MONDAY.isoformat()}").json()["goals"][0]
    assert goal["count"] == 2 and goal["reached"] is True and goal["guideline_goal"] is False


def test_two_goals_a_week_and_no_repeats(client):
    day = MONDAY.isoformat()
    assert client.post("/goals", json={"kind": "water_days", "day": day, "target_days": 3}).status_code == 200
    assert client.post("/goals", json={"kind": "water_days", "day": day, "target_days": 4}).status_code == 409
    body = client.post("/goals", json={"kind": "sleep_nights", "day": day, "target_days": 3}).json()
    assert body["suggestion"] is None
    assert client.post("/goals", json={"kind": "cook_nights", "day": day, "target_days": 1}).status_code == 409


def test_goals_are_private_and_removable(client):
    goal_id = client.post("/goals", json={"kind": "cook_nights", "day": MONDAY.isoformat(), "target_days": 1}).json()["goals"][0]["id"]
    other = {"X-User-Id": "sam"}
    assert client.get(f"/goals?day={MONDAY.isoformat()}", headers=other).json()["goals"] == []
    assert client.delete(f"/goals/{goal_id}", headers=other).status_code == 404
    assert client.delete(f"/goals/{goal_id}").status_code == 200
