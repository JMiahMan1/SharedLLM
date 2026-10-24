"""The usage route: identity is established here, then the day is forwarded.

A caller can never name another person's usage -- the body's own ``user`` is
ignored and the authenticated identity is what telemetry stores.
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


def _patch_telemetry(monkeypatch, payload=None, status=200, allowed=True):
    captured = {"calls": [], "rule": []}
    telemetry_svc = str(gateway_main.TELEMETRY_SVC)
    identity_svc = str(gateway_main.IDENTITY_SVC)

    class _Resp:
        def __init__(self, body=None):
            self.status = status
            self._payload = body if body is not None else (payload if payload is not None else {})
            self._text = json.dumps(self._payload)

        async def text(self):
            return self._text

        async def json(self, content_type=None):
            return self._payload

    class _Client:
        async def _record(self, verb, url, **kwargs):
            if url.startswith(identity_svc):
                captured["rule"].append(kwargs.get("params"))
                return _Resp({"allowed": allowed})
            if url.startswith(telemetry_svc):
                captured["calls"].append(
                    {
                        "verb": verb,
                        "url": url,
                        "json": kwargs.get("json"),
                        "params": kwargs.get("params"),
                    }
                )
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

    monkeypatch.setattr(gateway_main, "borrow_http_client", fake)
    return captured


def test_usage_forwards_the_authenticated_user(client, monkeypatch):
    payload = {"status": "SUCCESS", "usage": {"day": "2026-10-23", "millis": 1000}}
    cap = _patch_telemetry(monkeypatch, payload=payload)

    r = client.post(
        "/api/usage",
        json={"day": "2026-10-23", "millis": 1000, "user": "someone_else"},
    )

    assert r.status_code == 200
    call = cap["calls"][0]
    assert call["url"].endswith("/api/telemetry/usage")
    body = call["json"]
    assert body["user"] == "u1"  # the caller, not the body's user
    assert body["day"] == "2026-10-23"
    assert body["millis"] == 1000
    assert body["source"] == "phone"


def test_usage_history_forwards_days(client, monkeypatch):
    cap = _patch_telemetry(monkeypatch, payload={"status": "SUCCESS", "days": []})

    r = client.get("/api/usage/kiddo?days=3")

    assert r.status_code == 200
    call = cap["calls"][0]
    assert call["verb"] == "get"
    assert call["url"].endswith("/api/telemetry/usage/kiddo")
    assert call["params"] == {"days": 3}


def test_usage_is_refused_to_anyone_the_rule_does_not_allow(client, monkeypatch):
    cap = _patch_telemetry(monkeypatch, payload={"status": "SUCCESS", "days": []}, allowed=False)

    r = client.get("/api/usage/kiddo")

    assert r.status_code == 403
    assert cap["rule"] == [{"viewer": "u1", "subject": "kiddo"}]
    assert cap["calls"] == []  # telemetry is never asked
