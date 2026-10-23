"""The activity beacon route: identity is established here, then forwarded.

A caller can never name the person an event belongs to -- the body's own
``user`` is ignored and the authenticated identity is what execution records.
"""
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


@pytest.fixture
def client(monkeypatch):
    """A client whose bearer token resolves to a known identity."""

    async def _resolve(_request):
        return {"user": "u1", "user_id": 7, "is_admin": False}

    async def _resolve_by_key(_api_key):
        return {"user": "u1", "user_id": 7, "is_admin": False}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _resolve)
    monkeypatch.setattr(gateway_main, "_resolve_strict_identity", _resolve_by_key)
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


def _patch_execution(monkeypatch, payload=None, status=200):
    captured = {"calls": []}
    exec_svc = str(gateway_main.EXECUTION_SVC)

    class _Resp:
        def __init__(self):
            self.status = status
            self._payload = payload if payload is not None else {}
            self._text = json.dumps(self._payload)

        async def text(self):
            return self._text

        async def json(self):
            return self._payload

        async def read(self):
            return self._text.encode()

    class _Client:
        async def _record(self, verb, url, **kwargs):
            if url.startswith(exec_svc):
                captured["calls"].append({"verb": verb, "url": url, "json": kwargs.get("json")})
            return _Resp()

        async def post(self, url, **kw):
            return await self._record("post", url, **kw)

        async def get(self, url, **kw):
            return await self._record("get", url, **kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return captured


def test_the_beacon_forwards_identity_and_event(client, monkeypatch):
    payload = {"status": "SUCCESS", "message": "Noted.", "service": "activity_feed"}
    cap = _patch_execution(monkeypatch, payload=payload)

    r = client.post(
        "/api/activity",
        json={"feature": "notes", "item": "/notes", "user": "someone_else"},
    )

    assert r.status_code == 200
    call = cap["calls"][0]
    assert call["url"].endswith("/execute/activity")
    body = call["json"]
    assert body["user_context"]["user"] == "u1"  # the caller, not the body's user
    assert body["feature"] == "notes"
    assert body["item"] == "/notes"
    assert body["event"] == "view"
