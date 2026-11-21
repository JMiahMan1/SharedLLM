"""The gateway's workspace terminal relay (browser <-> workspace runtime).

The runtime's own endpoint is covered in services/workspace_runtime/tests;
this checks the hop in between: keystrokes and resizes reach the runtime,
output reaches the browser, and when the runtime ends the session its reason
reaches the browser instead of a bare "closed".
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager

import pytest
import websockets
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.frames import Close

from services.gateway import main


class _Resp:
    def __init__(self, status):
        self.status = status


class _Identity:
    def __init__(self, status=200):
        self.status = status

    async def get(self, url, headers=None, **kw):
        assert headers["Authorization"] == "Bearer good-token"
        return _Resp(self.status)


class _Runtime:
    """Stands in for the runtime's terminal socket."""

    def __init__(self, replies, close_code=None, close_reason=""):
        self.sent: list[str] = []
        self._replies = list(replies)
        self._close = (close_code, close_reason)
        self.url = None

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self._replies:
            return self._replies.pop(0)
        await asyncio.sleep(0.05)
        code, reason = self._close
        if code is None:
            await asyncio.Event().wait()
        raise websockets.ConnectionClosed(Close(code, reason), None)


def _patch(monkeypatch, runtime, identity_status=200):
    @asynccontextmanager
    async def http():
        yield _Identity(identity_status)

    @asynccontextmanager
    async def connect(url, **kw):
        runtime.url = url
        yield runtime

    monkeypatch.setattr(main, "borrow_http_client", http)
    monkeypatch.setattr(websockets, "connect", connect)


def test_keystrokes_and_output_pass_through(monkeypatch):
    runtime = _Runtime([json.dumps({"type": "stdout", "data": "raven@box:/w$ "})])
    _patch(monkeypatch, runtime)
    client = TestClient(main.app)
    with client.websocket_connect("/api/workspaces/demo/terminal?token=good-token") as ws:
        assert json.loads(ws.receive_text())["data"] == "raven@box:/w$ "
        ws.send_text("echo hi\r")
        ws.send_text(json.dumps({"type": "resize", "width": 120, "height": 30}))
        for _ in range(50):
            if len(runtime.sent) == 2:
                break
            time.sleep(0.02)
    assert runtime.sent == ["echo hi\r", json.dumps({"type": "resize", "width": 120, "height": 30})]
    assert runtime.url.endswith("/ws/workspace/demo/terminal?token=good-token")


def test_the_runtimes_reason_reaches_the_browser(monkeypatch):
    runtime = _Runtime([], close_code=1011, close_reason="Docker unavailable")
    _patch(monkeypatch, runtime)
    client = TestClient(main.app)
    with client.websocket_connect("/api/workspaces/demo/terminal?token=good-token") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    assert closed.value.code == 1011
    assert closed.value.reason == "Docker unavailable"


def test_an_invalid_token_is_refused(monkeypatch):
    _patch(monkeypatch, _Runtime([]), identity_status=401)
    client = TestClient(main.app)
    with client.websocket_connect("/api/workspaces/demo/terminal?token=good-token") as ws, pytest.raises(WebSocketDisconnect) as closed:
        ws.receive_text()
    assert closed.value.code == 1008
