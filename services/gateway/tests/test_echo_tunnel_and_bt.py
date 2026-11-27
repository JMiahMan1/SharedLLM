"""Unit tests for services/gateway/echo_tunnel_routes.py and
services/gateway/fast_echo_bluetooth.py - tunnel routes + BT matcher."""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from services.gateway import automation_routes
from services.gateway import main as gateway_main
from services.gateway.fast_echo_bluetooth import (
    EchoBluetoothCommand,
    match_echo_bluetooth,
    resolve_bt_entities,
)

# --- Test fixtures ---


@pytest.fixture
def client(monkeypatch):
    """FastAPI TestClient for the gateway app."""
    from services.gateway.main import app

    test_client = TestClient(app)
    return test_client


@pytest.fixture(autouse=True)
def _set_identity(monkeypatch):
    """Set up Identity service and borrow_http_client for auth tests."""
    monkeypatch.setenv("IDENTITY_SVC_URL", "http://identity:8000")


@pytest.fixture
def hub(monkeypatch):
    """Mock automation_routes._hub to return controlled responses."""
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
def admin(monkeypatch):
    """Ensure admin auth passes."""
    async def _always_admin(request):
        return True

    monkeypatch.setattr(automation_routes, "_admin", _always_admin)


# --- BT Matcher tests ---


def test_match_pair_on():
    result = match_echo_bluetooth("pair bluetooth")
    assert result is not None
    assert result.action == "pair_on"
    assert result.target_room is None
    assert isinstance(result, EchoBluetoothCommand)


def test_match_pair_off():
    result = match_echo_bluetooth("stop bluetooth pairing")
    assert result is not None
    assert result.action == "pair_off"


def test_match_disconnect():
    result = match_echo_bluetooth("disconnect bluetooth")
    assert result is not None
    assert result.action == "disconnect"


def test_match_status():
    result = match_echo_bluetooth("bluetooth status")
    assert result is not None
    assert result.action == "status"


def test_match_with_target_room():
    result = match_echo_bluetooth("pair bluetooth in the kitchen")
    assert result is not None
    assert result.target_room == "kitchen"

    result = match_echo_bluetooth("disconnect bluetooth on kitchen echo")
    assert result is not None
    assert result.target_room == "kitchen"


def test_no_match():
    from services.gateway.fast_echo_bluetooth import match_echo_bluetooth

    assert match_echo_bluetooth("") is None
    assert match_echo_bluetooth("drop in on the kitchen") is None
    assert match_echo_bluetooth("turn on the lights") is None


# --- Entity resolution tests ---


def test_resolve_bt_entities():
    states = {
        "switch.home_bluetooth_pairing": "on",
        "button.home_bluetooth_disconnect": "pressed",
        "sensor.home_bluetooth_device": "Pixel 6",
        "binary_sensor.home_bluetooth_connected": "on",
    }
    result = resolve_bt_entities(states, "home")
    assert result["pairing_switch"] == "switch.home_bluetooth_pairing"
    assert result["disconnect_button"] == "button.home_bluetooth_disconnect"
    assert result["connected_device"] == "sensor.home_bluetooth_device"
    assert result["connected_sensor"] == "binary_sensor.home_bluetooth_connected"


def test_resolve_bt_entities_no_match():
    states = {"switch.home_light": "on"}
    result = resolve_bt_entities(states, "home")
    assert result["pairing_switch"] is None
    assert result["disconnect_button"] is None
    assert result["connected_device"] is None
    assert result["connected_sensor"] is None


# --- Route tests ---


def test_echo_tunnel_status_route_registered():
    """Verify echo_tunnel routes are registered in the app."""
    route_paths = [r.path for r in gateway_main.app.routes if hasattr(r, "path")]
    # Routes under /api/homehub/echo_tunnel prefix
    [p for p in route_paths if "echo_tunnel" in p.lower()]
    assert len(gateway_main.app.routes) > 0


def test_echo_tunnel_status_forbidden(client, hub, monkeypatch):
    """GET /api/homehub/echo_tunnel/status returns 403 without admin."""
    seen, _ = hub

    async def _never_admin(request):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin only")

    # The fixture undoes this after the test; a bare MonkeyPatch() leaked the fake into
    # every later test in the shard (test_helper_routes saw "Admin only").
    monkeypatch.setattr(automation_routes, "_admin", _never_admin)

    resp = client.get("/api/homehub/echo_tunnel/status")
    assert resp.status_code in (401, 403)
    assert seen == []


def test_echo_tunnel_status_ok(client, hub, admin):
    """GET /api/homehub/echo_tunnel/status returns 200 with admin."""
    seen, _ = hub
    resp = client.get("/api/homehub/echo_tunnel/status")
    assert resp.status_code == 200
    assert seen == [("GET", "/hub/echo_tunnel/status", "secret", None)]


def test_echo_tunnel_disconnect_valid(client, hub, admin):
    """POST /api/homehub/echo_tunnel/disconnect/{node} with valid node."""
    seen, _ = hub
    resp = client.post("/api/homehub/echo_tunnel/disconnect/home")
    assert resp.status_code == 200
    assert seen == [("POST", "/hub/echo_tunnel/disconnect/home", "secret", None)]


def test_echo_tunnel_disconnect_invalid_node(client):
    """POST /api/homehub/echo_tunnel/disconnect/{node} with invalid node regex."""
    resp = client.post("/api/homehub/echo_tunnel/disconnect/Invalid Node!")
    assert resp.status_code == 404


def test_tokens_routes_require_admin(client, hub):
    """The token admin routes refuse without admin and never reach the hub."""
    seen, _ = hub

    async def _never_admin(request):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin only")

    mp = pytest.MonkeyPatch()
    mp.setattr(automation_routes, "_admin", _never_admin)
    try:
        assert client.get("/api/homehub/echo_tunnel/tokens").status_code in (401, 403)
        assert client.post("/api/homehub/echo_tunnel/tokens/home").status_code in (401, 403)
        assert client.delete("/api/homehub/echo_tunnel/tokens/home").status_code in (401, 403)
    finally:
        mp.undo()
    assert seen == []


def test_tokens_routes_proxy_with_admin(client, hub, admin):
    """With admin, the token routes proxy to HomeHub with the internal secret."""
    seen, _ = hub
    resp = client.post("/api/homehub/echo_tunnel/tokens/home")
    assert resp.status_code == 200
    assert seen[-1] == ("POST", "/hub/echo_tunnel/tokens/home", "secret", None)

    resp = client.delete("/api/homehub/echo_tunnel/tokens/home")
    assert resp.status_code == 200
    assert seen[-1] == ("DELETE", "/hub/echo_tunnel/tokens/home", "secret", None)

    resp = client.get("/api/homehub/echo_tunnel/tokens")
    assert resp.status_code == 200
    assert seen[-1] == ("GET", "/hub/echo_tunnel/tokens", "secret", None)


def test_tokens_create_invalid_node(client, admin):
    """POST /api/homehub/echo_tunnel/tokens/{node} with invalid node regex is a 404."""
    resp = client.post("/api/homehub/echo_tunnel/tokens/Invalid Node!")
    assert resp.status_code == 404


# --- WebSocket route test ---


def test_echo_tunnel_ws_route_registered():
    """WS /api/homehub/echo_tunnel/ws/{node} route is registered."""
    routes = [r.path for r in gateway_main.app.routes if hasattr(r, "path")]
    [r for r in routes if "echo_tunnel" in r.lower() and "ws" in r.lower()]
    assert len(gateway_main.app.routes) > 0


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
    from contextlib import asynccontextmanager

    import websockets

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
            if hasattr(upstream, "url"):
                upstream.url = url
            upstream.headers = kw.get("additional_headers") or kw.get("extra_headers")
            yield upstream

    monkeypatch.setattr(gateway_main, "borrow_http_client", http)
    monkeypatch.setattr(websockets, "connect", connect)
    import services.common.hub_login as hl
    monkeypatch.setattr(hl, "homehub_url", lambda: homehub)
    monkeypatch.setattr(gateway_main, "IDENTITY_SVC", "http://identity.test")
    return upstream


def test_ws_invalid_node(monkeypatch):
    _patch_ws(monkeypatch)
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_tunnel/ws/BadNode?token=good-token") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    assert closed.value.code == 1008


def test_ws_missing_token(monkeypatch):
    _patch_ws(monkeypatch)
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_tunnel/ws/node1") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    assert closed.value.code == 1008


def test_ws_invalid_token_forwarded(monkeypatch):
    """An invalid bearer token is forwarded upstream, not rejected by the gateway."""
    upstream = _Upstream(replies=[])
    _patch_ws(monkeypatch, upstream=upstream, identity_status=401)
    client = TestClient(gateway_main.app)
    with client.websocket_connect(
        "/api/homehub/echo_tunnel/ws/node1", headers={"Authorization": "Bearer bad"}
    ) as ws:
        ws.close()
    assert upstream.headers == {"Authorization": "Bearer bad"}


def test_ws_non_admin_token_forwarded(monkeypatch):
    """A valid non-admin bearer token is forwarded upstream as Authorization, not the internal secret."""
    upstream = _Upstream(replies=[])
    _patch_ws(monkeypatch, upstream=upstream, identity_status=200, identity_body={"is_admin": False})
    client = TestClient(gateway_main.app)
    with client.websocket_connect(
        "/api/homehub/echo_tunnel/ws/node1", headers={"Authorization": "Bearer device-tok"}
    ) as ws:
        ws.close()
    assert upstream.headers == {"Authorization": "Bearer device-tok"}
    assert "X-Internal-Secret" not in (upstream.headers or {})


def test_ws_unreachable_identity(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def http():
        raise RuntimeError("down")
        yield

    class _CM:
        async def __aenter__(self):
            raise RuntimeError("down")

        async def __aexit__(self, *a):
            return False

    def http_factory():
        return _CM()

    import websockets
    monkeypatch.setattr(gateway_main, "borrow_http_client", http_factory)
    import services.common.hub_login as hl
    monkeypatch.setattr(hl, "homehub_url", lambda: "http://hub.local")
    monkeypatch.setattr(gateway_main, "IDENTITY_SVC", "http://identity.test")

    @asynccontextmanager
    async def connect(url, **kw):
        yield _Upstream()

    monkeypatch.setattr(websockets, "connect", connect)

    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_tunnel/ws/node1?token=good") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    assert closed.value.code == 1011


def test_ws_empty_homehub(monkeypatch):
    _patch_ws(monkeypatch, homehub="")
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_tunnel/ws/node1?token=good-token") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    assert closed.value.code == 1011


def test_ws_relays_text_and_binary(monkeypatch):
    upstream = _Upstream(replies=[json.dumps({"type": "connected"})])
    upstream._replies.append(b"\x01\x02\x03")
    _patch_ws(monkeypatch, upstream=upstream)
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_tunnel/ws/node1?token=good-token") as ws:
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
    assert "/hub/echo_tunnel/ws/node1" in getattr(upstream, "url", "")


def test_ws_header_token(monkeypatch):
    upstream = _Upstream(replies=[])
    _patch_ws(monkeypatch, upstream=upstream)
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_tunnel/ws/node1", headers={"Authorization": "Bearer good-token"}) as ws:
        ws.close()


def test_ws_admin_body_without_is_admin_forwarded(monkeypatch):
    """A token whose identity body lacks is_admin is forwarded upstream, not rejected locally."""
    upstream = _Upstream(replies=[])
    _patch_ws(monkeypatch, upstream=upstream, identity_status=200, identity_body={"user": "kid"})
    client = TestClient(gateway_main.app)
    with client.websocket_connect(
        "/api/homehub/echo_tunnel/ws/node1", headers={"Authorization": "Bearer kid"}
    ) as ws:
        ws.close()
    assert upstream.headers == {"Authorization": "Bearer kid"}


def test_ws_trailing_slash_homehub_url(monkeypatch):
    upstream = _Upstream(replies=[])
    _patch_ws(monkeypatch, upstream=upstream, homehub="http://hub.local:8124/")
    client = TestClient(gateway_main.app)
    with client.websocket_connect("/api/homehub/echo_tunnel/ws/node1?token=good-token") as ws:
        ws.close()
    assert getattr(upstream, "url", "") == "ws://hub.local:8124/hub/echo_tunnel/ws/node1"


# ---------- Echo Bluetooth fast-path tests ----------


def _make_kitchen_states():
    return [
        {"entity_id": "switch.kitchen_echo_bluetooth_pairing", "state": "off"},
        {"entity_id": "button.kitchen_echo_bluetooth_disconnect", "state": "idle"},
        {"entity_id": "sensor.kitchen_echo_bluetooth_device", "state": "Pixel"},
    ]


@pytest.mark.asyncio
async def test_bt_pair_on_kitchen(monkeypatch):
    from services.gateway import main as gw_main

    calls = []

    async def fake_hub(method, path, *, creds=None, params=None, json=None, timeout=None):
        calls.append((method, path, json))
        if path == "/api/states":
            return 200, _make_kitchen_states()
        return 200, {}

    monkeypatch.setattr(gw_main.automation_routes, "_hub", fake_hub)

    result = await gw_main._fast_echo_bluetooth_answer(
        "turn on bluetooth pairing in the kitchen", {}, None
    )
    assert result == "Bluetooth pairing is on for two minutes. Pick the Echo in your phone's Bluetooth settings."
    assert any(c[1] == "/api/states" for c in calls)
    assert any(c[1] == "/api/services/switch/turn_on" for c in calls)
    turn_on_json = next(c[2] for c in calls if c[1] == "/api/services/switch/turn_on")
    assert turn_on_json["entity_id"] == "switch.kitchen_echo_bluetooth_pairing"


@pytest.mark.asyncio
async def test_bt_disconnect_via_satellite(monkeypatch):
    from services.gateway import main as gw_main

    calls = []

    async def fake_hub(method, path, *, creds=None, params=None, json=None, timeout=None):
        calls.append((method, path, json))
        if path == "/api/states":
            return 200, _make_kitchen_states()
        return 200, {}

    monkeypatch.setattr(gw_main.automation_routes, "_hub", fake_hub)

    voice_ctx = {"satellite": "assist_satellite.kitchen_echo_assist_satellite"}
    result = await gw_main._fast_echo_bluetooth_answer(
        "disconnect bluetooth", {}, voice_ctx
    )
    assert result == "Disconnected."
    press_json = next(c[2] for c in calls if c[1] == "/api/services/button/press")
    assert press_json["entity_id"] == "button.kitchen_echo_bluetooth_disconnect"


@pytest.mark.asyncio
async def test_bt_status_kitchen(monkeypatch):
    from services.gateway import main as gw_main

    async def fake_hub(method, path, *, creds=None, params=None, json=None, timeout=None):
        if path == "/api/states":
            return 200, _make_kitchen_states()
        return 200, {}

    monkeypatch.setattr(gw_main.automation_routes, "_hub", fake_hub)

    result = await gw_main._fast_echo_bluetooth_answer(
        "bluetooth status in the kitchen", {}, None
    )
    assert result == "Pixel is connected."


@pytest.mark.asyncio
async def test_bt_unrelated_query_returns_none(monkeypatch):
    from services.gateway import main as gw_main

    calls = []

    async def fake_hub(method, path, *, creds=None, params=None, json=None, timeout=None):
        calls.append((method, path))
        return 200, {}

    monkeypatch.setattr(gw_main.automation_routes, "_hub", fake_hub)

    result = await gw_main._fast_echo_bluetooth_answer(
        "turn on the living room lights", {}, None
    )
    assert result is None
    assert calls == []


@pytest.mark.asyncio
async def test_bt_room_matches_only_echos_and_uses_the_callers_login(monkeypatch):
    """"kitchen" must pick the kitchen Echo, not a kitchen light, and every hub call carries the caller's creds."""
    from services.gateway import main as gw_main

    seen_creds = []
    states = [{"entity_id": "light.kitchen_ceiling", "state": "on"}, *_make_kitchen_states()]

    async def fake_hub(method, path, *, creds=None, params=None, json=None, timeout=None):
        seen_creds.append(creds)
        if path == "/api/states":
            return 200, states
        return 200, {}

    monkeypatch.setattr(gw_main.automation_routes, "_hub", fake_hub)
    caller = {"user": "child-account", "role": "user"}
    result = await gw_main._fast_echo_bluetooth_answer("turn on bluetooth pairing in the kitchen", caller, None)
    assert result.startswith("Bluetooth pairing is on")
    assert seen_creds and all(c is caller for c in seen_creds)


@pytest.mark.asyncio
async def test_bt_a_refused_hub_call_is_not_reported_as_done(monkeypatch):
    from services.gateway import main as gw_main

    async def fake_hub(method, path, *, creds=None, params=None, json=None, timeout=None):
        if path == "/api/states":
            return 200, _make_kitchen_states()
        return 403, {"message": "forbidden"}

    monkeypatch.setattr(gw_main.automation_routes, "_hub", fake_hub)
    result = await gw_main._fast_echo_bluetooth_answer("turn on bluetooth pairing in the kitchen", {}, None)
    assert result == "Could not reach that Echo right now."
