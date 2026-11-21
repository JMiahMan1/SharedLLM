"""The chat long-poll route: it runs as the caller and holds long enough."""
import json
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


def test_wait_forwards_as_the_caller_with_a_long_enough_timeout(monkeypatch):
    async def _acting(_request, _as_user=None):
        return {"user": "kiddo", "nextcloud_user": "kiddo"}

    monkeypatch.setattr(gateway_main, "_resolve_acting_identity", _acting)
    calls: list[dict] = []

    class _Resp:
        status = 200

        async def text(self):
            return json.dumps({"status": "SUCCESS", "detail": {"messages": [{"id": 9}]}})

    class _Client:
        async def post(self, url, **kw):
            calls.append({"url": url, **kw})
            return _Resp()

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)

    r = TestClient(app).get("/api/communication/talk/messages/wait?token=room-1&last_id=8&wait=99")

    assert r.status_code == 200 and r.json()["detail"]["messages"] == [{"id": 9}]
    body = calls[0]["json"]
    assert body["user_context"]["user"] == "kiddo"
    assert (body["action"], body["token"], body["last_known_id"], body["wait_s"]) == ("wait", "room-1", 8, 30)
    assert calls[0]["timeout"].total > 30


def test_a_history_page_passes_has_more_through(monkeypatch):
    """Execution counts "is there more" on Talk's raw page; the gateway must not drop or recount it."""
    async def _acting(_request, _as_user=None):
        return {"user": "kiddo", "nextcloud_user": "kiddo"}

    monkeypatch.setattr(gateway_main, "_resolve_acting_identity", _acting)
    calls: list[dict] = []

    class _Resp:
        status = 200

        async def text(self):
            return json.dumps({"status": "SUCCESS", "detail": {"messages": [{"id": 9}], "last_id": 12, "has_more": True}})

    class _Client:
        async def post(self, url, **kw):
            calls.append({"url": url, **kw})
            return _Resp()

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)

    r = TestClient(app).get("/api/communication/talk/messages?token=room-1&limit=50")

    assert r.status_code == 200
    assert r.json()["detail"]["has_more"] is True
    assert (calls[0]["json"]["action"], calls[0]["json"]["limit"]) == ("messages", 50)
