"""The activity feed: what each person did inside Jarvis (a profile reads it back)."""
import pytest
from fakes import FakeRedis
from fastapi.testclient import TestClient

from services.telemetry import store
from services.telemetry.config import INTERNAL_SECRET as EXPECTED_SECRET
from services.telemetry.main import app

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


def test_use_summarizes_in_app_features_without_the_raw_stream(client, rc):
    for feature in ("media", "music", "bible", "sms"):
        client.post(
            "/api/telemetry/activity",
            headers=HEADERS,
            json={"user": "kiddo", "feature": feature, "item": "x"},
        )
    body = client.get("/api/telemetry/use/kiddo", headers=HEADERS).json()["use"]
    # Music/video/podcasts group into one line; the phone's own collection (sms) is left out.
    assert body["top"] == [{"label": "Music, video and podcasts", "count": 2}, {"label": "Bible", "count": 1}]
    assert body["total"] == 3
    assert "events" not in body


def test_use_needs_the_internal_secret(client):
    assert client.get("/api/telemetry/use/kiddo").status_code == 401



def test_a_call_keeps_the_time_it_happened_not_the_upload_time(client, rc):
    from datetime import UTC, datetime, timedelta

    happened = datetime.now(UTC) - timedelta(hours=5)
    body = {"user": "kiddo", "feature": "calls", "item": "Grandma",
            "detail": {"direction": "in", "at": int(happened.timestamp() * 1000)}}
    event = client.post("/api/telemetry/activity", headers=HEADERS, json=body).json()["event"]
    assert abs(datetime.fromisoformat(event["at"]) - happened) < timedelta(seconds=1)

    # A time in the future, or far in the past, is not believed.
    for bad in (int((datetime.now(UTC) + timedelta(days=1)).timestamp() * 1000), 1, True, "yesterday"):
        event = client.post("/api/telemetry/activity", headers=HEADERS,
                            json={**body, "detail": {"at": bad}}).json()["event"]
        assert abs(datetime.fromisoformat(event["at"]) - datetime.now(UTC)) < timedelta(minutes=1)
