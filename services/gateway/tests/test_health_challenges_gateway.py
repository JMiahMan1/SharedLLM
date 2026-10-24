"""Health proxy: acts as the caller and tells health whether they are a minor.

The flag rides on every health call (kid mode is forced for minors, and only
adults start a family challenge); a definite answer is cached for five minutes.
"""
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status = body, status

    async def json(self, content_type=None):
        return self._body


@pytest.fixture
def calls(monkeypatch):
    """Record health and identity calls; identity's answer is set per test."""
    recorded: dict = {"health": [], "identity": [], "managed": False, "identity_status": 200}
    monkeypatch.setattr(gateway_main, "_HEALTH_MINOR_CACHE", {})

    async def _ident(_request):
        return {"user": "mom", "is_admin": False}

    monkeypatch.setattr(gateway_main, "_acting_identity", _ident)

    class _Health:
        async def request(self, method, url, **kw):
            recorded["health"].append((method, url, kw))
            return _Resp({"ok": True})

    @asynccontextmanager
    async def fake_health():
        yield _Health()

    class _Identity:
        async def get(self, url, **kw):
            recorded["identity"].append((url, kw))
            if recorded["identity_status"] != 200:
                return _Resp({}, status=recorded["identity_status"])
            return _Resp({"rules": {}, "bonus_min": 0, "managed": recorded["managed"]})

    @asynccontextmanager
    async def fake_identity():
        yield _Identity()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake_health)
    monkeypatch.setattr(gateway_main, "borrow_http_client", fake_identity)
    return recorded


@pytest.fixture
def client():
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


def test_listing_sends_the_adult_flag(client, calls):
    assert client.get("/api/health/challenges", params={"day": "2026-10-21"}).json() == {"ok": True}
    method, url, kw = calls["health"][0]
    assert method == "GET" and url.endswith("/challenges")
    assert kw["params"] == {"day": "2026-10-21"}
    assert kw["headers"]["X-User-Id"] == "mom"
    assert kw["headers"]["X-User-Is-Minor"] == "false"
    assert calls["identity"][0][0].endswith("/api/internal/screen-time/mom")


def test_a_minor_is_flagged(client, calls):
    calls["managed"] = True
    client.post("/api/health/challenges", json={"kind": "water_days", "day": "2026-10-21", "target_days": 3})
    method, _url, kw = calls["health"][0]
    assert method == "POST" and kw["json"]["kind"] == "water_days"
    assert kw["headers"]["X-User-Is-Minor"] == "true"


def test_identity_down_is_unknown_not_adult(client, calls):
    calls["identity_status"] = 503
    client.post("/api/health/challenges", json={"kind": "water_days", "day": "2026-10-21", "target_days": 3})
    assert calls["health"][0][2]["headers"]["X-User-Is-Minor"] == "unknown"


def test_every_health_call_carries_the_flag_and_identity_is_asked_once(client, calls):
    calls["managed"] = True
    client.post("/api/health/challenges/4/join")
    client.delete("/api/health/challenges/4/join")
    client.get("/api/health/profile")
    assert [c[0] for c in calls["health"]] == ["POST", "DELETE", "GET"]
    assert all(c[2]["headers"]["X-User-Is-Minor"] == "true" for c in calls["health"])
    assert len(calls["identity"]) == 1


def test_an_identity_outage_is_not_remembered(client, calls):
    calls["identity_status"] = 503
    client.get("/api/health/profile")
    calls["identity_status"] = 200
    client.get("/api/health/profile")
    assert [c[2]["headers"]["X-User-Is-Minor"] for c in calls["health"]] == ["unknown", "false"]
    assert len(calls["identity"]) == 2


def test_anonymous_is_refused(calls, monkeypatch):
    async def _nobody(_request):
        return None

    monkeypatch.setattr(gateway_main, "_acting_identity", _nobody)
    assert TestClient(app).get("/api/health/challenges").status_code == 401
    assert calls["health"] == []
