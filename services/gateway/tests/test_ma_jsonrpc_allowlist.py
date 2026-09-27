"""BUG-04 / P1-T4: MA JSON-RPC command allowlist + admin-only debug routes.

- Browser frames on WS /api/ma-jsonrpc may only carry commands from the §7.3
  allowlist; anything else is answered locally with
  {"error_code": "forbidden", "message_id": <same id>} and never reaches MA.
- The /api/ma-jsonrpc/debug/* routes require an admin identity.
"""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import websockets

import services.gateway.main as gateway_main

DEBUG_PATHS = [
    "/api/ma-jsonrpc/debug/players",
    "/api/ma-jsonrpc/debug/queues",
    "/api/ma-jsonrpc/debug/player/p1",
]


def test_config_command_rejected():
    from services.gateway.ma_allowlist import validate_ma_frame

    raw = json.dumps({"message_id": "m1", "command": "config/integrations", "args": {}})
    err = validate_ma_frame(raw)
    assert err is not None
    frame = json.loads(err)
    assert frame["error_code"] == "forbidden"
    assert frame["message_id"] == "m1"


def test_allowlisted_play_media_forwarded():
    from services.gateway.ma_allowlist import validate_ma_frame

    raw = json.dumps({"message_id": "m2", "command": "player_queues/play_media", "args": {"queue_id": "p1"}})
    assert validate_ma_frame(raw) is None


def test_malformed_frame_rejected():
    from services.gateway.ma_allowlist import validate_ma_frame

    err = validate_ma_frame("not json{{{")
    assert err is not None
    assert json.loads(err)["error_code"] == "forbidden"


@pytest.mark.parametrize("path", DEBUG_PATHS)
def test_debug_route_forbidden_for_non_admin(client, monkeypatch, path):
    monkeypatch.setattr(
        gateway_main,
        "_resolve_identity_from_request",
        AsyncMock(return_value={"user": "testuser", "is_admin": False}),
    )
    monkeypatch.setattr(
        gateway_main, "_resolve_ma_credentials", AsyncMock(return_value=("", ""))
    )
    resp = client.get(path)
    assert resp.status_code == 403


@pytest.mark.parametrize("path", DEBUG_PATHS)
def test_debug_route_admin_passes_gate(client, monkeypatch, path):
    monkeypatch.setattr(
        gateway_main, "_resolve_ma_credentials", AsyncMock(return_value=("", ""))
    )
    resp = client.get(path)
    # Admin gate passed; the request fails later at "MA token not configured".
    assert resp.status_code == 400


class _FakeMaWs:
    """Minimal stand-in for a MA server WebSocket.

    recv() polls the queue and a threading.Event so the test can release the
    gateway's MA→browser loop from the test thread without touching loop
    primitives.
    """

    def __init__(self, holder: dict):
        import threading

        self.holder = holder
        self.sent: list[dict] = []
        self.queue: asyncio.Queue = asyncio.Queue()
        self.close_event = threading.Event()
        holder["ws"] = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, text: str) -> None:
        data = json.loads(text)
        if data.get("message_id") == "gateway-auth":
            await self.queue.put(
                json.dumps({"type": "auth/ok", "message_id": "gateway-auth"})
            )
            return
        self.sent.append(data)
        await self.queue.put(
            json.dumps({"message_id": data.get("message_id"), "result": {"ok": True}})
        )

    async def recv(self):
        while True:
            try:
                return self.queue.get_nowait()
            except asyncio.QueueEmpty:
                if self.close_event.is_set():
                    raise RuntimeError("fake MA closed")
                await asyncio.sleep(0.05)

    async def close(self) -> None:
        pass


def test_ws_proxy_forwards_allowlisted_and_rejects_config(client, monkeypatch):
    """End-to-end: config/... answered with an error frame (never reaches MA);
    player_queues/play_media is forwarded to MA."""
    holder: dict = {}

    def fake_connect(*args, **kwargs):
        return _FakeMaWs(holder)

    monkeypatch.setattr(websockets, "connect", fake_connect)

    with client.websocket_connect("/api/ma-jsonrpc?token=test-key") as ws:
        auth_frame = json.loads(ws.receive_text())
        assert auth_frame.get("type") == "auth/ok"

        ws.send_text(json.dumps({"message_id": "m1", "command": "config/integrations", "args": {}}))
        err = json.loads(ws.receive_text())
        assert err["error_code"] == "forbidden"
        assert err["message_id"] == "m1"

        ws.send_text(json.dumps({"message_id": "m2", "command": "player_queues/play_media", "args": {"queue_id": "p1"}}))
        echo = json.loads(ws.receive_text())
        assert echo["message_id"] == "m2"
        assert echo.get("result") == {"ok": True}

        fake_ws = holder.get("ws")
        assert fake_ws is not None, "gateway never opened the MA WebSocket"
        commands = [f.get("command") for f in fake_ws.sent]
        assert "player_queues/play_media" in commands
        assert "config/integrations" not in commands
        # Release the gateway's MA→browser loop before closing the browser.
        fake_ws.close_event.set()
