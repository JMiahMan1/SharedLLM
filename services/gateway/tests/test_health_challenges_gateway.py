"""Family challenge proxy: acts as the caller and tells health whether they are a minor."""
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


def test_join_leave_and_delete_proxy_without_a_role_lookup(client, calls):
    client.post("/api/health/challenges/4/join")
    client.delete("/api/health/challenges/4/join")
    client.delete("/api/health/challenges/4")
    assert [c[1].rsplit("/challenges", 1)[1] for c in calls["health"]] == ["/4/join", "/4/join", "/4"]
    assert [c[0] for c in calls["health"]] == ["POST", "DELETE", "DELETE"]
    assert calls["identity"] == []
    assert all("X-User-Is-Minor" not in c[2]["headers"] for c in calls["health"])


def test_anonymous_is_refused(calls, monkeypatch):
    async def _nobody(_request):
        return None

    monkeypatch.setattr(gateway_main, "_acting_identity", _nobody)
    assert TestClient(app).get("/api/health/challenges").status_code == 401
    assert calls["health"] == []
