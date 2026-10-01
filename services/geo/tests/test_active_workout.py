"""Tests for `GET /workouts/active` in the geo service.

An in-progress workout is deliberately absent from `geo:workouts:user:{u}` --
it only enters that index when it is stopped. Without this endpoint a client
could only discover a running session by scanning the history list for a
`status` value the server never emits, so the feature was unreachable.
"""

import json

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo
from services.config import INTERNAL_SECRET


class _FakeRedis:
    """Just enough Redis for these routes.

    The zset methods return empty rather than raising so a test can exercise
    `/workouts` (which reads a sorted set) alongside the plain-string
    `geo:active_workout:{user}` key without building a full fake.
    """

    def __init__(self, store=None):
        self.store = store or {}
        self.zsets: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def zrevrange(self, name, start, stop):
        return self.zsets.get(name, [])[start : stop + 1] if stop >= 0 else []

    async def zrevrangebyscore(self, name, max_score, min_score, start=None, num=None):
        return []

    async def zrange(self, name, start, stop, withscores=False):
        return []

    async def zrangebyscore(self, name, lo, hi, withscores=False):
        return []

    async def zcard(self, name):
        return len(self.zsets.get(name, []))

    async def hgetall(self, name):
        return {}


def _active(user: str) -> str:
    return json.dumps(
        {
            "id": f"workout_{user}_1700000000",
            "user_id": user,
            "user_name": user.title(),
            "activity_type": "walking",
            "start_time": 1700000000.0,
            "end_time": None,
            "status": "in_progress",
        }
    )


@pytest.fixture
def client(monkeypatch):
    store: dict = {}

    async def _sharing():
        return {"users": []}

    monkeypatch.setattr(geo, "_fetch_activity_sharing", _sharing)
    monkeypatch.setattr(geo, "get_redis", lambda: _ready(_FakeRedis(store)))
    return TestClient(
        geo.app, headers={"X-Internal-Secret": INTERNAL_SECRET}
    ), store


async def _ready(redis):
    return redis


def test_a_running_workout_is_returned(client):
    test_client, store = client
    store["geo:active_workout:michele"] = _active("michele")

    resp = test_client.get(
        "/workouts/active",
        params={"viewer": "michele"},
        headers={"X-User-Id": "michele"},
    )
    assert resp.status_code == 200
    body = resp.json()["workout"]
    assert body["activity_type"] == "walking"
    # The real status string, which the old client scan never matched.
    assert body["status"] == "in_progress"


def test_no_running_workout_is_404(client):
    test_client, _ = client
    resp = test_client.get(
        "/workouts/active",
        params={"viewer": "michele"},
        headers={"X-User-Id": "michele"},
    )
    assert resp.status_code == 404


def test_it_requires_the_internal_secret(client):
    test_client, store = client
    store["geo:active_workout:michele"] = _active("michele")
    bare = TestClient(geo.app)
    assert bare.get(
        "/workouts/active", headers={"X-User-Id": "michele"}
    ).status_code == 403


def test_you_cannot_read_another_users_running_workout(client):
    """Without consent, the answer is 404 -- not just empty."""
    test_client, store = client
    store["geo:active_workout:jeremiah"] = _active("jeremiah")

    resp = test_client.get(
        "/workouts/active",
        params={"viewer": "michele"},
        headers={"X-User-Id": "jeremiah"},
    )
    assert resp.status_code == 404


def test_opted_in_user_can_read_the_running_workout(client, monkeypatch):
    async def _sharing():
        return {
            "users": [
                {
                    "username": "jeremiah",
                    "enabled": True,
                    "audience": "circle",
                    "user_ids": [],
                }
            ]
        }

    monkeypatch.setattr(geo, "_fetch_activity_sharing", _sharing)
    test_client, store = client
    store["geo:active_workout:jeremiah"] = _active("jeremiah")

    resp = test_client.get(
        "/workouts/active",
        params={"viewer": "michele"},
        headers={"X-User-Id": "jeremiah"},
    )
    assert resp.status_code == 200
    assert resp.json()["workout"]["user_id"] == "jeremiah"


def test_an_admin_can_read_it_regardless_of_consent(client):
    test_client, store = client
    store["geo:active_workout:jeremiah"] = _active("jeremiah")

    resp = test_client.get(
        "/workouts/active",
        params={"viewer": "michele", "is_admin": "true"},
        headers={"X-User-Id": "jeremiah"},
    )
    assert resp.status_code == 200


def test_it_requires_an_identifiable_caller(client):
    test_client, _ = client
    resp = test_client.get("/workouts/active")
    assert resp.status_code == 400


def test_a_stored_workout_is_not_in_the_history_list(client):
    """The gap this endpoint closes, pinned as an explicit property.

    A running session is not in the history index, so listing workouts cannot
    reveal it -- which is why a client-side scan could never work.
    """
    test_client, store = client
    store["geo:active_workout:michele"] = _active("michele")

    listed = test_client.get(
        "/workouts",
        params={"user_id": "michele", "viewer": "michele"},
    )
    assert listed.status_code == 200
    assert listed.json()["workouts"] == []