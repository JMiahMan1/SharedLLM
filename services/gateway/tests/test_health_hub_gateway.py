"""Health hub proxies added for the later phases: each acts as the caller only."""
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
    recorded: dict = {"health": []}

    async def _ident(_request):
        return {"user": "dad", "is_admin": False}

    monkeypatch.setattr(gateway_main, "_acting_identity", _ident)

    class _Health:
        async def request(self, method, url, **kw):
            recorded["health"].append((method, url, kw))
            return _Resp({"ok": True})

    @asynccontextmanager
    async def fake_health():
        yield _Health()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake_health)
    return recorded


@pytest.fixture
def client():
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


@pytest.mark.parametrize(
    ("method", "path", "body", "upstream"),
    [
        ("GET", "/api/health/streaks", None, "/streaks"),
        ("GET", "/api/health/fasting", None, "/fasting"),
        ("POST", "/api/health/fasting/start", {"target_hours": 14}, "/fasting/start"),
        ("POST", "/api/health/fasting/stop", {}, "/fasting/stop"),
        ("DELETE", "/api/health/fasting/3", None, "/fasting/3"),
    ],
)
def test_routes_forward_as_the_caller(client, calls, method, path, body, upstream):
    response = client.request(method, path, json=body)
    assert response.json() == {"ok": True}
    sent_method, url, kw = calls["health"][0]
    assert sent_method == method and url.endswith(upstream)
    assert kw["headers"]["X-User-Id"] == "dad"
    if body is not None:
        assert kw["json"] == body


def test_active_minutes_composes_geo_and_the_health_store(client, monkeypatch):
    async def _ident(_request):
        return {"user": "dad", "is_admin": False}

    monkeypatch.setattr(gateway_main, "_acting_identity", _ident)
    monkeypatch.setenv("TIMEZONE", "UTC")
    seen: list[tuple[str, dict]] = []

    class _Routed:
        async def request(self, method, url, **kw):
            seen.append((url, kw))
            if url.endswith("/steps/sources"):
                return _Resp({"hourly": {"phone": {"2026-10-20:07": 4000}}})
            if url.endswith("/workouts"):
                return _Resp({"workouts": []})
            if url.endswith("/profile"):
                return _Resp({"birth_year": 1986, "weekly_active_minutes": 150})
            return _Resp({"samples": []})

    @asynccontextmanager
    async def fake():
        yield _Routed()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    body = client.get("/api/health/active-minutes", params={"day": "2026-10-21"}).json()
    assert body["total_minutes"] == 30
    assert body["target_minutes"] == 150 and body["percent"] == 20
    assert body["unavailable"] == []
    steps_call = next(kw for url, kw in seen if url.endswith("/steps/sources"))
    assert steps_call["params"]["user_id"] == "dad" and steps_call["params"]["days"] == 3
    assert client.get("/api/health/active-minutes", params={"day": "21-10-2026"}).status_code == 422
