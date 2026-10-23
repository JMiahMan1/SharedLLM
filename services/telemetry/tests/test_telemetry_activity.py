"""The activity feed: what each person did inside Jarvis (a profile reads it back)."""
import pytest
from fastapi.testclient import TestClient

from services.telemetry import store
from services.telemetry.config import INTERNAL_SECRET as EXPECTED_SECRET
from services.telemetry.main import app

from fakes import FakeRedis

HEADERS = {"X-Internal-Secret": EXPECTED_SECRET}


@pytest.fixture
def rc(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("services.telemetry.main._redis", lambda: fake)
    return fake


@pytest.fixture
def client():
    # Lifespan is skipped so no scheduler/worker loops run during tests.
    with TestClient(app) as c:
        yield c


def test_records_and_reads_back_an_event(client, rc):
    resp = client.post(
        "/api/telemetry/activity",
        headers=HEADERS,
        json={"user": "kiddo", "feature": "music", "item": "Way Maker", "action": "play"},
    )
    assert resp.status_code == 200
    event = resp.json()["event"]
    assert event["user"] == "kiddo"
    assert event["at"]  # stamped with the current time

    listed = client.get("/api/telemetry/activity/kiddo", headers=HEADERS).json()["events"]
    assert len(listed) == 1
    assert listed[0]["feature"] == "music"
    assert listed[0]["item"] == "Way Maker"


def test_newest_events_come_first(client, rc):
    for i in range(3):
        client.post(
            "/api/telemetry/activity",
            headers=HEADERS,
            json={"user": "kiddo", "feature": "game", "item": f"game {i}", "action": "finish"},
        )

    listed = client.get("/api/telemetry/activity/kiddo?limit=2", headers=HEADERS).json()["events"]
    assert [e["item"] for e in listed] == ["game 2", "game 1"]


def test_requires_user_and_feature(client, rc):
    assert client.post(
        "/api/telemetry/activity", headers=HEADERS, json={"feature": "music"}
    ).status_code == 422
    assert client.post(
        "/api/telemetry/activity", headers=HEADERS, json={"user": "kiddo"}
    ).status_code == 422


def test_requires_the_internal_secret(client):
    assert client.post(
        "/api/telemetry/activity", json={"user": "x", "feature": "music"}
    ).status_code == 401
    assert client.get("/api/telemetry/activity/x").status_code == 401


def test_feed_is_capped(client, rc, monkeypatch):
    monkeypatch.setattr(store, "ACTIVITY_MAX", 3)
    for i in range(5):
        client.post(
            "/api/telemetry/activity",
            headers=HEADERS,
            json={"user": "kiddo", "feature": "page", "item": f"page {i}"},
        )

    listed = client.get("/api/telemetry/activity/kiddo", headers=HEADERS).json()["events"]
    assert [e["item"] for e in listed] == ["page 4", "page 3", "page 2"]
