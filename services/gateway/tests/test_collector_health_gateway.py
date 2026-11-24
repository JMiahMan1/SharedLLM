"""Collection health through the gateway: a phone reports as its signed-in
owner, and only someone allowed the calls and messages reads it."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.gateway import collector_health_routes as routes
from services.gateway import main as gateway_main
from services.gateway.main import app

HEALTH = {"status": "SUCCESS", "user": "kid", "devices": [],
          "staleness": {"comms": {"stale": True, "last_at": "2026-11-21T19:00:00+00:00"}}}


@pytest.fixture
def wired(monkeypatch):
    who = {"user": "kid"}
    access = {"allowed": True, "comms": True}
    telemetry: list[tuple] = []
    answers: dict = {}

    async def _auth(request):
        if who["user"] is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        return who["user"]

    async def _raw_access(request, subject):
        if not access["allowed"]:
            raise HTTPException(status_code=403, detail="Only a parent can see this")
        return dict(access)

    async def _telemetry(method, path, **kw):
        telemetry.append((method, path, kw.get("json")))
        return answers.get((method, path), (200, {"status": "SUCCESS"}))

    monkeypatch.setattr(gateway_main, "_require_authenticated", _auth)
    monkeypatch.setattr(gateway_main, "_raw_access", _raw_access)
    monkeypatch.setattr(routes, "telemetry_call", _telemetry)
    return {"client": TestClient(app, headers={"Authorization": "Bearer key"}), "who": who, "access": access,
            "telemetry": telemetry, "answers": answers}


def test_a_phone_reports_as_its_signed_in_owner_and_extra_fields_are_dropped(wired):
    r = wired["client"].post("/api/telemetry/device_health", json={
        "user": "someone-else", "device_id": "dev-0001", "permissions": {"read_sms": False},
        "workers": {"comms_sync": "CANCELLED"}, "sms_bodies": ["never kept"],
    })
    assert r.status_code == 200, r.text
    method, path, sent = wired["telemetry"][0]
    assert (method, path) == ("POST", "/api/telemetry/device_health")
    assert sent["user"] == "kid"
    assert "sms_bodies" not in sent
    assert sent["permissions"] == {"read_sms": False}


def test_reporting_needs_a_signed_in_phone(wired):
    wired["who"]["user"] = None
    assert wired["client"].post("/api/telemetry/device_health", json={"device_id": "dev-0001"}).status_code == 401
    assert wired["telemetry"] == []


def test_a_report_telemetry_refuses_is_a_422(wired):
    wired["answers"][("POST", "/api/telemetry/device_health")] = (422, {"detail": "device_id is required"})
    r = wired["client"].post("/api/telemetry/device_health", json={})
    assert r.status_code == 422


def test_a_parent_reads_a_childs_collection_health(wired):
    wired["who"]["user"] = "mom"
    wired["answers"][("GET", "/api/telemetry/device_health/kid")] = (200, HEALTH)
    r = wired["client"].get("/api/guardian/collector-health/kid")
    assert r.status_code == 200, r.text
    assert r.json()["staleness"]["comms"]["stale"] is True


def test_someone_without_the_calls_and_messages_cannot(wired):
    wired["access"]["comms"] = False
    assert wired["client"].get("/api/guardian/collector-health/kid").status_code == 403
    wired["access"]["allowed"] = False
    assert wired["client"].get("/api/guardian/collector-health/kid").status_code == 403
    assert wired["telemetry"] == []


def test_a_bad_username_is_refused(wired):
    assert wired["client"].get("/api/guardian/collector-health/a%20b").status_code == 422


def test_telemetry_down_is_a_502(wired):
    wired["answers"][("GET", "/api/telemetry/device_health/kid")] = (503, {"detail": "down"})
    assert wired["client"].get("/api/guardian/collector-health/kid").status_code == 502
