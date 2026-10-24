"""The podcast recommendations route: a suggestion derived from the member's listening.

The raw listening feed stays parents-only; only the derived suggestion is
returned to the signed-in member.
"""
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


@pytest.fixture
def client(monkeypatch):
    async def _resolve(_request):
        return {"user": "u1", "user_id": 7, "is_admin": False}

    async def _resolve_by_key(_api_key):
        return {"user": "u1", "user_id": 7, "is_admin": False}

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _resolve)
    monkeypatch.setattr(gateway_main, "_resolve_strict_identity", _resolve_by_key)
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


def _patch_telemetry(monkeypatch, payload=None, status=200):
    captured = {"calls": []}
    telemetry_svc = str(gateway_main.TELEMETRY_SVC)

    class _Resp:
        def __init__(self):
            self.status = status
            self._payload = payload if payload is not None else {}

        async def text(self):
            import json
            return json.dumps(self._payload)

        async def json(self, content_type=None):
            return self._payload

    class _Client:
        async def _record(self, verb, url, **kwargs):
            if url.startswith(telemetry_svc):
                captured["calls"].append({"verb": verb, "url": url, "params": kwargs.get("params")})
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
    holder = {"payload": payload, "status": status}
    orig_record = _Client._record

    async def record(self, verb, url, **kwargs):
        r = await orig_record(self, verb, url, **kwargs)
        r._payload = holder["payload"] if holder["payload"] is not None else {}
        r.status = holder["status"]
        return r

    _Client._record = record
    return captured, holder


def test_podcast_recommendation_returns_suggestion(client, monkeypatch):
    cap, holder = _patch_telemetry(monkeypatch)
    holder["payload"] = {
        "status": "SUCCESS",
        "events": [
            {"feature": "media", "item": "The Joe Rogan Experience #2000", "detail": {"show": "The Joe Rogan Experience", "app": "Spotify"}},
        ],
    }
    r = client.get("/api/recommendations/podcast")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "SUCCESS"
    assert body["recommendation"]["show"] == "That Sounds Fun"
    assert body["recommendation"]["redeemed"] is True
    assert cap["calls"][0]["url"].endswith("/api/telemetry/activity/u1")


def test_podcast_recommendation_quiet_without_listening(client, monkeypatch):
    _cap, holder = _patch_telemetry(monkeypatch)
    holder["payload"] = {"status": "SUCCESS", "events": []}
    r = client.get("/api/recommendations/podcast")
    assert r.status_code == 200
    assert r.json()["recommendation"] is None


def test_podcast_recommendation_requires_auth(monkeypatch):
    async def _anon(_request):
        return None

    monkeypatch.setattr(gateway_main, "_resolve_identity_from_request", _anon)
    c = TestClient(app)
    r = c.get("/api/recommendations/podcast")
    assert r.status_code == 401
