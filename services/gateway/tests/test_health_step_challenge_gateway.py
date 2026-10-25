"""Step challenges: health lists the members, the gateway scores them from geo.

Steps live in geo, so the gateway reads each member's daily steps and own step
goal (the internal read the active-minutes week uses) and ranks them with the
health service's challenge rules. A member geo cannot answer for is shown as
unavailable, never as zero days.
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

WEEK = "2026-10-19"  # a Monday


def _step_challenge(*members: str) -> dict:
    return {
        "id": 7,
        "kind": "step_days",
        "title": "Step goal days",
        "week_start": WEEK,
        "week_end": "2026-10-25",
        "target_days": 2,
        "leaderboard": [
            {"user_id": user, "count": 0, "days": [], "guideline_goal": False, "unavailable": True, "reached": False}
            for user in members
        ],
    }


@pytest.fixture
def wired(monkeypatch):
    state: dict = {"health": None, "geo": {}, "geo_calls": []}

    async def fake_health(method, path, request, **kw):
        return state["health"]

    async def fake_geo(path, params):
        state["geo_calls"].append((path, params))
        return state["geo"].get(params["user_id"])

    monkeypatch.setattr(gateway_main, "_health_json", fake_health)
    monkeypatch.setattr(gateway_main, "_geo_get", fake_geo)
    # Thursday of the challenge week.
    monkeypatch.setattr(gateway_main, "_household_now", lambda: datetime(2026, 10, 22, 12, 0))
    return state


@pytest.fixture
def client():
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


def test_members_are_scored_from_their_own_goal_and_ranked(client, wired):
    wired["health"] = {"week_start": WEEK, "challenges": [_step_challenge("mom", "sam")]}
    wired["geo"] = {
        "mom": {"goal": 8000, "daily_steps": {"2026-10-19": 9000, "2026-10-20": 7999, "2026-10-21": 8000}},
        "sam": {"goal": 12000, "daily_steps": {"2026-10-18": 30000, "2026-10-20": 12500}},
    }
    board = client.get("/api/health/challenges", params={"day": "2026-10-22"}).json()["challenges"][0]["leaderboard"]
    assert board == [
        {"user_id": "mom", "count": 2, "days": ["2026-10-19", "2026-10-21"], "guideline_goal": False,
         "unavailable": False, "reached": True},
        {"user_id": "sam", "count": 1, "days": ["2026-10-20"], "guideline_goal": False,
         "unavailable": False, "reached": False},
    ]
    # Read as the internal caller, back to the Monday only.
    assert ("/steps", {"user_id": "mom", "days": 4}) in wired["geo_calls"]


def test_geo_down_or_without_a_goal_is_unavailable_not_zero(client, wired):
    wired["health"] = {"challenges": [_step_challenge("mom", "sam")]}
    wired["geo"] = {"sam": {"daily_steps": {}, "today": 0}}  # geo without Redis has no goal
    board = client.get("/api/health/challenges").json()["challenges"][0]["leaderboard"]
    assert [row["unavailable"] for row in board] == [True, True]
    assert [row["count"] for row in board] == [0, 0]


def test_other_kinds_pass_through_untouched(client, wired):
    water = {**_step_challenge("mom"), "kind": "water_days"}
    wired["health"] = {"challenges": [water]}
    assert client.get("/api/health/challenges").json()["challenges"] == [water]
    assert wired["geo_calls"] == []


def test_join_and_create_are_scored_too(client, wired):
    wired["health"] = _step_challenge("mom")
    wired["geo"] = {"mom": {"goal": 5000, "daily_steps": {"2026-10-22": 6000}}}
    joined = client.post("/api/health/challenges/7/join").json()
    assert joined["leaderboard"][0]["days"] == ["2026-10-22"]
    created = client.post("/api/health/challenges", json={"kind": "step_days", "day": WEEK, "target_days": 2}).json()
    assert created["leaderboard"][0]["count"] == 1


def test_a_week_that_has_not_started_reads_nothing(client, wired, monkeypatch):
    monkeypatch.setattr(gateway_main, "_household_now", lambda: datetime(2026, 10, 18, 12, 0))
    wired["health"] = _step_challenge("mom")
    row = client.post("/api/health/challenges/7/join").json()["leaderboard"][0]
    assert row["count"] == 0 and row["unavailable"] is False
    assert wired["geo_calls"] == []
