import os

os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("FERNET_KEY", "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3ZlcyE=")

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


def test_health_is_public(client):
    assert client.get("/health").json()["status"] == "ok"


def test_endpoints_require_internal_secret(client):
    assert client.get("/api/telemetry/schedules/jeremiah").status_code == 401
    assert client.get("/api/telemetry/schedules/jeremiah", headers={"X-Internal-Secret": "wrong"}).status_code == 401


def test_schedule_can_be_created_and_read_back(client, rc):
    resp = client.put(
        "/api/telemetry/schedules/jeremiah",
        headers=HEADERS,
        json={"type": "health", "period": "daily", "run_at": "21:00", "timezone": "America/Phoenix"},
    )
    assert resp.status_code == 200
    job = resp.json()["job"]
    assert job["run_at"] == "21:00"
    assert job["enabled"] is True

    listed = client.get("/api/telemetry/schedules/jeremiah", headers=HEADERS).json()["jobs"]
    assert len(listed) == 1
    assert listed[0]["period"] == "daily"


def test_schedule_update_reuses_same_job(client, rc):
    body = {"type": "power", "period": "monthly", "run_at": "20:00", "timezone": "UTC"}
    first = client.put("/api/telemetry/schedules/jeremiah", headers=HEADERS, json=body).json()["job"]
    second = client.put("/api/telemetry/schedules/jeremiah", headers=HEADERS, json=body).json()["job"]
    assert first["id"] == second["id"]
    jobs = client.get("/api/telemetry/schedules/jeremiah", headers=HEADERS).json()["jobs"]
    assert len(jobs) == 1


def test_schedule_rejects_unknown_type_and_period(client, rc):
    bad_type = client.put(
        "/api/telemetry/schedules/u", headers=HEADERS, json={"type": "nope", "period": "daily"}
    )
    bad_period = client.put(
        "/api/telemetry/schedules/u", headers=HEADERS, json={"type": "health", "period": "hourly"}
    )
    assert bad_type.status_code == 422
    assert bad_period.status_code == 422


def test_schedules_are_isolated_per_user(client, rc):
    client.put("/api/telemetry/schedules/jeremiah", headers=HEADERS, json={"type": "health", "period": "daily"})
    client.put("/api/telemetry/schedules/work", headers=HEADERS, json={"type": "health", "period": "daily"})
    mine = client.get("/api/telemetry/schedules/jeremiah", headers=HEADERS).json()["jobs"]
    theirs = client.get("/api/telemetry/schedules/work", headers=HEADERS).json()["jobs"]
    assert len(mine) == 1 and len(theirs) == 1
    assert mine[0]["user"] == "jeremiah"
    assert theirs[0]["user"] == "work"


def test_on_demand_request_is_queued(client, rc):
    resp = client.post(
        "/api/telemetry/reports/request",
        headers=HEADERS,
        json={"user": "jeremiah", "type": "health", "period": "weekly"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "QUEUED"
    # the run is actually sitting in the queue for the worker
    assert body["job_id"] in rc.zsets[store.QUEUE]


def test_on_demand_request_requires_user(client, rc):
    assert client.post("/api/telemetry/reports/request", headers=HEADERS, json={}).status_code == 422


def test_reports_listing_and_latest(client, rc):
    import anyio

    async def seed():
        rc_ = rc
        await store.save_report(rc_, {
            "id": "r1", "user": "jeremiah", "type": "health", "period": "daily",
            "status": "ready", "analysis": "ok", "stats": {}, "generated_ts": 1,
        })

    anyio.run(seed)

    listed = client.get("/api/telemetry/reports/jeremiah", headers=HEADERS).json()["reports"]
    assert len(listed) == 1
    latest = client.get(
        "/api/telemetry/reports/jeremiah/latest?type=health", headers=HEADERS
    ).json()["report"]
    assert latest["id"] == "r1"


def test_notifications_endpoint(client, rc):
    import anyio

    async def seed():
        await store.push_notification(rc, "jeremiah", {
            "id": "n1", "kind": "report_ready", "title": "hi", "read": False,
        })

    anyio.run(seed)
    body = client.get("/api/telemetry/notifications/jeremiah", headers=HEADERS).json()
    assert body["notifications"][-1]["kind"] == "report_ready"
