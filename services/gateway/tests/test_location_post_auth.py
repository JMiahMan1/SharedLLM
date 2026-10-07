"""Posting a location: your own, or anyone's for an admin.

The gateway adds Identity's internal secret, and used to forward any caller's
post, so anyone who could reach Jarvis could put anyone anywhere on the map.
"""
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


def _setup(monkeypatch, ident):
    async def acting(request):
        return ident
    monkeypatch.setattr(gateway_main, "_acting_identity", acting)
    sent = []

    class _Resp:
        status = 200

        async def json(self):
            return {"status": "SUCCESS"}

    class _Client:
        async def post(self, url, **kw):
            if url.startswith(str(gateway_main.IDENTITY_SVC)):  # request logging shares the client
                sent.append((url, kw.get("json")))
            return _Resp()

    @asynccontextmanager
    async def fake():
        yield _Client()
    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return sent


FIX = {"latitude": 33.1, "longitude": -111.5, "accuracy": 6, "timestamp": 1791000000}


def test_anonymous_posts_are_refused(monkeypatch):
    sent = _setup(monkeypatch, None)
    client = TestClient(app)
    assert client.post("/api/users/michele/location", json=FIX).status_code == 401
    assert client.post("/api/users/location", json={**FIX, "user_id": "michele"}).status_code == 401
    assert sent == []


def test_you_may_post_only_your_own(monkeypatch):
    sent = _setup(monkeypatch, {"user": "jeremiah", "is_admin": False})
    client = TestClient(app)
    assert client.post("/api/users/michele/location", json=FIX).status_code == 403
    assert client.post("/api/users/Jeremiah/location", json=FIX).status_code == 200
    # the collection route ignores a user_id a non-admin names
    assert client.post("/api/users/location", json={**FIX, "user_id": "michele"}).status_code == 200
    assert sent[-1][0].endswith("/api/users/jeremiah/location")


def test_an_admin_may_post_for_someone(monkeypatch):
    sent = _setup(monkeypatch, {"user": "jeremiah", "is_admin": True})
    assert TestClient(app).post("/api/users/michele/location", json=FIX).status_code == 200
    assert sent[-1][0].endswith("/api/users/michele/location")
