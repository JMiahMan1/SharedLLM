"""Echo drop-in routes: admin-gated status/hangup and the WebSocket proxy."""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest
import websockets
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from services.gateway import automation_routes, echo_dropin_routes
from services.gateway import main as gateway_main


@pytest.fixture
def hub(monkeypatch):
    seen = []
    answers = {}

    async def call(method, path, *, creds=None, params=None, json=None, timeout=None):
        seen.append((method, path, "secret" if creds is None else "caller", json))
        if path in answers:
            return answers[path]
        return 200, {"ok": True}

    monkeypatch.setattr(automation_routes, "_hub", call)
    return seen, answers


@pytest.fixture
def member(monkeypatch):
    async def not_admin(request):
        raise __import__("fastapi").HTTPException(status_code=403, detail="Admin only")

    monkeypatch.setattr(automation_routes, "_admin", not_admin)


@pytest.fixture
def admin(monkeypatch):
    async def _always_admin(request):
        return True

    monkeypatch.setattr(automation_routes, "_admin", _always_admin)


def test_status_forwards_for_admin(client, hub, admin):
    seen, _ = hub
    resp = client.get("/api/homehub/echo_dropin/status")
    assert resp.status_code == 200
    assert seen == [("GET", "/hub/echo_dropin/status", "secret", None)]


def test_status_rejects_non_admin(client, hub, member):
    seen, _ = hub
    resp = client.get("/api/homehub/echo_dropin/status")
    assert resp.status_code == 403
    assert seen == []


def test_hangup_validates_hex(client, hub):
    seen, _ = hub
    for bad in ["xyz", "../etc", "gg", "", "a" * 65]:
        # empty path param not routable; skip empty
        if bad == "":
            continue
        resp = client.post(f"/api/homehub/echo_dropin/hangup/{bad}")
        assert resp.status_code == 404, bad
    assert seen == []


def test_hangup_forwards_valid_hex(client, hub, admin):
    seen, _ = hub
    resp = client.post("/api/homehub/echo_dropin/hangup/abc123")
    assert resp.status_code == 200
    assert seen == [("POST", "/hub/echo_dropin/hangup/abc123", "secret", None)]


def test_hangup_rejects_non_admin(client, hub, member):
    seen, _ = hub
    resp = client.post("/api/homehub/echo_dropin/hangup/abc123")
    assert resp.status_code == 403
    assert seen == []


# ---------- WebSocket ----------


class _Upstream:
    def __init__(self, replies=None, close_code=None):
        self.sent = []
        self._replies = list(replies or [])
        self._close = close_code
        self.closed = False
        self.url = None

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self._replies:
            return self._replies.pop(0)
        await asyncio.sleep(0.05)
        if self._close is not None:
            raise websockets.ConnectionClosed(
                websockets.frames.Close(self._close, ""), None
            )
        await asyncio.Event().wait()

    async def close(self):
        self.closed = True


def _patch_ws(monkeypatch, upstream=None, identity_status=200, identity_body=None, homehub="http://hub.local", connect_exc=None):
    @asynccontextmanager
    async def http():
        class _Resp:
            def __init__(self):
                self.status = identity_status
                self._body = identity_body if identity_body is not None else ({"is_admin": True} if identity_status == 200 else None)

            async def json(self, content_type=None):
                return self._body

        class _Client:
            async def get(self, url, headers=None, **kw):
                return _Resp()

        yield _Client()

    if connect_exc is not None:
        def connect(url, **kw):
            raise connect_exc
    else:
        if upstream is None:
            upstream = _Upstream()

        @asynccontextmanager
        async def connect(url, **kw):
            # capture url on upstream if possible
            if hasattr(upstream, "url"):
                upstream.url = url
            yield upstream

    monkeypatch.setattr(gateway_main, "borrow_http_client", http)
    monkeypatch.setattr(websockets, "connect", connect)
    monkeypatch.setattr(echo_dropin_routes, "homehub_url", lambda: homehub)
    # also patch canonical module
    import services.common.hub_login as hl
    monkeypatch.setattr(hl, "homehub_url", lambda: homehub)
    monkeypatch.setattr(gateway_main, "IDENTITY_SVC", "http://identity.test")
    return upstream


def test_ws_invalid_node(monkeypatch):
    _patch_ws(monkeypatch)
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_dropin/ws/BadNode?token=good-token") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    assert closed.value.code == 1008


def test_ws_missing_token(monkeypatch):
    _patch_ws(monkeypatch)
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_dropin/ws/node1") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    assert closed.value.code == 1008


def test_ws_invalid_token(monkeypatch):
    _patch_ws(monkeypatch, identity_status=401)
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_dropin/ws/node1?token=bad") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    assert closed.value.code == 1008


def test_ws_non_admin_token(monkeypatch):
    _patch_ws(monkeypatch, identity_status=200, identity_body={"is_admin": False})
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_dropin/ws/node1?token=good") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    assert closed.value.code == 1008


def test_ws_unreachable_identity(monkeypatch):
    # make borrow_http_client raise
    @asynccontextmanager
    async def http():
        raise RuntimeError("down")
        yield

    # http as async context manager that raises on entry
    class _CM:
        async def __aenter__(self):
            raise RuntimeError("down")

        async def __aexit__(self, *a):
            return False

    def http_factory():
        return _CM()

    monkeypatch.setattr(gateway_main, "borrow_http_client", http_factory)
    import services.common.hub_login as hl
    monkeypatch.setattr(hl, "homehub_url", lambda: "http://hub.local")
    monkeypatch.setattr(echo_dropin_routes, "homehub_url", lambda: "http://hub.local")
    monkeypatch.setattr(gateway_main, "IDENTITY_SVC", "http://identity.test")
    import websockets as _ws

    @asynccontextmanager
    async def connect(url, **kw):
        yield _Upstream()

    monkeypatch.setattr(_ws, "connect", connect)

    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_dropin/ws/node1?token=good") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    assert closed.value.code == 1011


def test_ws_empty_homehub(monkeypatch):
    _patch_ws(monkeypatch, homehub="")
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_dropin/ws/node1?token=good-token") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    assert closed.value.code == 1011


def test_ws_relays_text_and_binary(monkeypatch):
    upstream = _Upstream(replies=[json.dumps({"type": "connected"})])
    # queue binary after text
    upstream._replies.append(b"\x01\x02\x03")
    _patch_ws(monkeypatch, upstream=upstream)
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_dropin/ws/node1?token=good-token") as ws:
        assert json.loads(ws.receive_text()) == {"type": "connected"}
        assert ws.receive_bytes() == b"\x01\x02\x03"
        ws.send_text(json.dumps({"type": "ping"}))
        ws.send_bytes(b"\x04\x05")
        for _ in range(50):
            if len(upstream.sent) == 2:
                break
            import time
            time.sleep(0.02)
    assert upstream.sent[0] == json.dumps({"type": "ping"})
    assert upstream.sent[1] == b"\x04\x05"
    assert "/hub/echo_dropin/ws/node1" in getattr(upstream, "url", "")


def test_ws_header_token(monkeypatch):
    upstream = _Upstream(replies=[])
    _patch_ws(monkeypatch, upstream=upstream)
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_dropin/ws/node1", headers={"Authorization": "Bearer good-token"}) as ws:
        # close quickly
        ws.close()
    # no exception


# ---------- Regression tests for fixed bugs ----------

def test_ws_admin_body_without_is_admin_rejected(monkeypatch):
    _patch_ws(monkeypatch, identity_status=200, identity_body={"user": "kid"})
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_dropin/ws/node1?token=good") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    assert closed.value.code == 1008


def test_ws_malformed_json_200_rejected(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def http():
        class _Resp:
            status = 200

            async def json(self, content_type=None):
                raise ValueError("not json")

        class _Client:
            async def get(self, url, headers=None, **kw):
                return _Resp()

        yield _Client()

    monkeypatch.setattr(gateway_main, "borrow_http_client", http)
    import services.common.hub_login as hl

    monkeypatch.setattr(hl, "homehub_url", lambda: "http://hub.local")
    monkeypatch.setattr(echo_dropin_routes, "homehub_url", lambda: "http://hub.local")
    monkeypatch.setattr(gateway_main, "IDENTITY_SVC", "http://identity.test")
    import websockets as _ws

    @asynccontextmanager
    async def connect(url, **kw):
        yield _Upstream()

    monkeypatch.setattr(_ws, "connect", connect)

    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_dropin/ws/node1?token=good") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    assert closed.value.code == 1008


def test_ws_trailing_slash_homehub_url(monkeypatch):
    upstream = _Upstream(replies=[])
    _patch_ws(monkeypatch, upstream=upstream, homehub="http://hub.local:8124/")
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_dropin/ws/node1?token=good-token") as ws:
        ws.close()
    assert getattr(upstream, "url", "") == "ws://hub.local:8124/hub/echo_dropin/ws/node1"
