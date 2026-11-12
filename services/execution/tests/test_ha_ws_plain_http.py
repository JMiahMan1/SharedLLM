"""The hub serves HA's WebSocket on plain http (ws://); the one-shot WS helpers must not force TLS on it."""
from __future__ import annotations

import json
import sys
import types

import pytest

from services.execution.handlers import ha_config


class _FakeWS:
    def __init__(self):
        self.sent = []
        self.replies = [{"type": "auth_required"}, {"type": "auth_ok"}, {"id": 1, "type": "result", "success": True,
                                                                         "result": []}]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        return json.dumps(self.replies.pop(0))


@pytest.mark.parametrize(("url", "tls"), [("http://hub.example.com:8123", False), ("https://ha.example.com", True)])
async def test_ws_request_uses_tls_only_for_https(monkeypatch, url, tls):
    seen = {}

    def connect(ws_url, ssl=None):
        seen["url"], seen["ssl"] = ws_url, ssl
        return _FakeWS()

    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=connect))
    out = await ha_config._ws_request(url, "token-for-tests", {"id": 1, "type": "config_entries/get"})
    assert out["success"] is True
    assert seen["url"].startswith("wss://" if tls else "ws://")
    assert (seen["ssl"] is not None) is tls
