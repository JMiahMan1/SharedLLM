import os

os.environ["INTERNAL_SECRET"] = "test-secret"

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo_main
from services.geo.main import app


class _FakeRedis:
    def __init__(self):
        self.strings = {}
        self.lists = {}

    async def get(self, key):
        return self.strings.get(key)

    async def set(self, key, value):
        self.strings[key] = value

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    async def lrange(self, key, start, end):
        items = self.lists.get(key, [])
        if end == -1:
            return items[start:]
        return items[start:end + 1]


@pytest.fixture(name="client")
def client_fixture(monkeypatch):
    redis = _FakeRedis()

    async def fake_redis():
        return redis

    monkeypatch.setattr(geo_main, "get_redis", fake_redis)
    return TestClient(app), redis


def test_grant_stars_updates_balance_and_ledger(client):
    test_client, redis = client
    resp = test_client.post(
        "/api/geo/stars",
        json={"user_id": "mom", "stars": 3, "reason": "bonus", "note": "helped with chores", "granted_by": "dad"},
        headers={"X-Internal-Secret": "test-secret"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stars"] == 3
    assert body["balance"] == 3
    assert body["reason"] == "bonus"
    assert body["granted_by"] == "dad"

    again = test_client.post(
        "/api/geo/stars",
        json={"user_id": "mom", "stars": 2, "reason": "achievement"},
        headers={"X-Internal-Secret": "test-secret"},
    )
    assert again.json()["balance"] == 5

    ledger = test_client.get(
        "/api/geo/stars", params={"user_id": "mom"}, headers={"X-Internal-Secret": "test-secret"}
    ).json()
    assert ledger["stars"] == 5
    assert [g["stars"] for g in ledger["grants"]] == [2, 3]


def test_cannot_deduct_more_than_the_balance(client):
    test_client, _ = client
    resp = test_client.post(
        "/api/geo/stars",
        json={"user_id": "kiddo", "stars": -2},
        headers={"X-Internal-Secret": "test-secret"},
    )
    assert resp.status_code == 422
    assert "star" in resp.json()["detail"]


def test_rejects_zero_unknown_reason_and_overflow(client):
    test_client, _ = client
    headers = {"X-Internal-Secret": "test-secret"}

    assert test_client.post("/api/geo/stars", json={"user_id": "mom", "stars": 0}, headers=headers).status_code == 422
    assert test_client.post(
        "/api/geo/stars", json={"user_id": "mom", "stars": 5, "reason": "because"}, headers=headers
    ).status_code == 422
    assert test_client.post(
        "/api/geo/stars", json={"user_id": "mom", "stars": 5000}, headers=headers
    ).status_code == 422
    assert test_client.post("/api/geo/stars", json={"stars": 5}, headers=headers).status_code == 400


def test_reading_ledger_tolerates_a_corrupt_entry(client):
    test_client, redis = client
    test_client.post(
        "/api/geo/stars",
        json={"user_id": "mom", "stars": 1, "reason": "game"},
        headers={"X-Internal-Secret": "test-secret"},
    )
    redis.lists["geo:stars_ledger:mom"].append("not-json")

    ledger = test_client.get(
        "/api/geo/stars", params={"user_id": "mom"}, headers={"X-Internal-Secret": "test-secret"}
    ).json()
    assert ledger["stars"] == 1
    assert len(ledger["grants"]) == 1
