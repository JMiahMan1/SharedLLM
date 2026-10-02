"""The gateway must proxy GET /api/geo/events.

Written against the gateway rather than geo, because the client only ever
talks to the gateway: an identity route (or a geo route) that the gateway does
not forward is a feature that is dead in production while its own tests stay
green. That has happened twice on this codebase.
"""
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app

RECIPIENTS = [
    {"username": "default", "display_name": "Default"},
    {"username": "jeremiah", "display_name": "Jeremiah"},
]


def _patch_identity(monkeypatch, status=200, payload=None, captured=None):
    """Capture what the gateway forwards to identity.

    ``shared_http_client`` also ships request logs, so a naive capture is
    overwritten by the logging POST. Only identity-bound calls are recorded.
    """
    captured = captured if captured is not None else {}
    captured["calls"] = []
    identity_svc = str(gateway_main.IDENTITY_SVC)

    class _Resp:
        def __init__(self):
            self.status = status
            self.text = json.dumps(payload if payload is not None else {})

        async def json(self):
            return payload if payload is not None else {}

        async def read(self):
            return json.dumps(payload if payload is not None else {}).encode()

    class _Client:
        async def _record(self, verb, url, **kwargs):
            if url.startswith(identity_svc):
                captured["calls"].append({"verb": verb, "url": url, **kwargs})
            return _Resp()

        async def get(self, url, **kw):
            return await self._record("get", url, **kw)

        async def post(self, url, **kw):
            return await self._record("post", url, **kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return captured


def _patch_geo(monkeypatch, status=200, payload=None, captured=None):
    captured = captured if captured is not None else {}
    captured["calls"] = []
    geo_svc = str(gateway_main.GEO_SVC)

    class _Resp:
        def __init__(self):
            self.status = status
            self.text = json.dumps(payload if payload is not None else {})

        async def json(self):
            return payload if payload is not None else {}

        async def read(self):
            return json.dumps(payload if payload is not None else {}).encode()

    class _Client:
        async def _record(self, verb, url, **kwargs):
            if url.startswith(geo_svc):
                captured["calls"].append(
                    {
                        "verb": verb,
                        "url": url,
                        "params": kwargs.get("params") or {},
                        "headers": kwargs.get("headers") or {},
                    }
                )
            return _Resp()

        async def get(self, url, **kw):
            return await self._record("get", url, **kw)

        async def post(self, url, **kw):
            return await self._record("post", url, **kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    @asynccontextmanager
    async def fake():
        yield _Client()

    monkeypatch.setattr(gateway_main, "shared_http_client", fake)
    return captured


@pytest.fixture
def make_client(monkeypatch):
    """Patches ``_resolve_strict_identity``: ``resolve_identity`` resolves any
    junk string to the default admin, so tests would pass for the wrong reason
    and the "viewer is not trusted from the caller" case would be vacuous."""

    def _make(user="jeremiah", is_admin=False, key="test-token"):
        async def _resolve(api_key):
            if api_key != key:
                return None
            return {"user": user, "user_id": 7, "is_admin": is_admin}

        monkeypatch.setattr(gateway_main, "_resolve_strict_identity", _resolve)
        return TestClient(app, headers={"Authorization": f"Bearer {key}"})

    return _make


@pytest.fixture
def client(make_client):
    return make_client()


class TestEventsProxy:
    def test_reaching_the_endpoint_is_not_a_404(self, client, monkeypatch):
        cap = _patch_geo(monkeypatch, payload={"user_id": "jeremiah", "groups": [], "empty": True})
        r = client.get("/api/geo/events?user_id=jeremiah")
        assert r.status_code == 200
        assert len(cap["calls"]) == 1

    def test_forwards_to_geo(self, client, monkeypatch):
        cap = _patch_geo(monkeypatch, payload={"empty": True})
        client.get("/api/geo/events?user_id=jeremiah")
        assert cap["calls"][0]["url"] == f"{gateway_main.GEO_SVC}/events"

    def test_forwards_the_requested_window(self, client, monkeypatch):
        cap = _patch_geo(monkeypatch, payload={"empty": True})
        client.get("/api/geo/events?user_id=jeremiah&days=90&limit=100")
        params = cap["calls"][0]["params"]
        assert params.get("days") == 90
        assert params.get("limit") == 100

    def test_sends_the_internal_secret(self, client, monkeypatch):
        cap = _patch_geo(monkeypatch, payload={"empty": True})
        client.get("/api/geo/events?user_id=jeremiah")
        assert cap["calls"][0]["headers"].get("X-Internal-Secret") == gateway_main.INTERNAL_SECRET

    def test_resolves_the_viewer_rather_than_trusting_the_caller(self, client, monkeypatch):
        # A client-supplied viewer must not be able to name someone else.
        cap = _patch_geo(monkeypatch, payload={"empty": True})
        client.get("/api/geo/events?user_id=jeremiah&viewer=admin&is_admin=true")
        params = cap["calls"][0]["params"]
        assert not params.get("is_admin")
        assert params.get("viewer") != "admin"

    def test_relays_an_empty_timeline_unchanged(self, client, monkeypatch):
        payload = {"user_id": "jeremiah", "groups": [], "empty": True, "day_count": 0}
        _patch_geo(monkeypatch, payload=payload)
        assert client.get("/api/geo/events?user_id=jeremiah").json() == payload

    def test_relays_geo_refusals_rather_than_laundering_them(self, client, monkeypatch):
        """A 404 from geo is a consent refusal; turning it into a 200 with an
        empty timeline would tell the user they have no history."""
        _patch_geo(monkeypatch, status=404, payload={"detail": "Not Found"})
        assert client.get("/api/geo/events?user_id=jeremiah").status_code == 404

    def test_relays_a_bad_window_rather_than_reporting_an_outage(self, client, monkeypatch):
        _patch_geo(monkeypatch, status=422, payload={"detail": "range must be one of D, W, M, 3M, Y"})
        r = client.get("/api/geo/events?user_id=jeremiah&days=999")
        assert r.status_code == 422

    def test_does_not_capture_the_path_as_a_parameter(self, client, monkeypatch):
        """A sibling /api/geo/{x} route must not swallow the literal 'events'."""
        cap = _patch_geo(monkeypatch, payload={"empty": True})
        client.get("/api/geo/events?user_id=jeremiah")
        assert len(cap["calls"]) == 1
        assert cap["calls"][0]["url"].endswith("/events")
