"""/api/groups/* adds Identity's internal secret, so it must check the caller
itself: it used to forward anyone, signed in or not."""
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


class _Resp:
    status = 200

    async def json(self):
        return []

    async def read(self):
        return b"[]"

    @property
    def headers(self):
        return {"Content-Type": "application/json"}


def _as(monkeypatch, ident):
    async def acting(request):
        return ident
    monkeypatch.setattr(gateway_main, "_acting_identity", acting)
    sent = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kw):
            sent.append((method, url))
            return _Resp()

    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _Client())

    async def proxied(resp):
        return {"ok": True}
    monkeypatch.setattr(gateway_main, "_proxy_json_response", proxied)
    return sent


def test_reading_groups_needs_a_signed_in_user(monkeypatch):
    sent = _as(monkeypatch, None)
    assert TestClient(app).get("/api/groups/lights").status_code == 401
    assert sent == []


def test_a_signed_in_user_can_read_groups(monkeypatch):
    sent = _as(monkeypatch, {"user": "michele", "is_admin": False})
    assert TestClient(app).get("/api/groups/lights").status_code == 200
    assert sent and sent[0][0] == "GET"


def test_only_an_admin_changes_groups(monkeypatch):
    sent = _as(monkeypatch, {"user": "michele", "is_admin": False})
    client = TestClient(app)
    assert client.post("/api/groups/lights", json={"name": "x"}).status_code == 403
    assert client.delete("/api/groups/lights/x").status_code == 403
    assert sent == []
    _as(monkeypatch, {"user": "jeremiah", "is_admin": True})
    assert client.delete("/api/groups/lights/x").status_code == 200
