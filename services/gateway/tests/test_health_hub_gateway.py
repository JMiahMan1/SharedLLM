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
