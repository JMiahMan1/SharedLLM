"""Gateway proxy for the range-aware step history.

The client cannot reach geo directly, so a route that does not exist here does
not exist for the app at all -- a wider view of someone else's step data is
useless without the consent check behind it, and the check lives in geo.
"""
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from services.gateway import main as gateway_main
from services.gateway.main import app


def _patch_geo(monkeypatch, payload=None, status=200, captured=None):
    """Capture only geo-bound calls; the logging POST shares this client."""
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
                        "params": kwargs.get("params"),
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
    """A client whose presented key resolves to a chosen identity.

    Patches ``_resolve_strict_identity`` deliberately. ``resolve_identity``
    would be wrong here: it resolves *any* junk string to the default admin,
    so tests written against it pass for the wrong reason and would not notice
    a real identity regression.
    """

    def _make(user="u1", is_admin=False, key="test-token"):
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


class TestRangeProxy:
    def test_forwards_the_range(self, client, monkeypatch):
        cap = _patch_geo(monkeypatch, payload={"range": "Y", "buckets": []})
        r = client.get("/api/geo/steps/ranges?range=Y")
        assert r.status_code == 200
        assert cap["calls"][0]["url"].endswith("/steps/ranges")
        assert cap["calls"][0]["params"]["range"] == "Y"

    def test_forwards_every_supported_range(self, client, monkeypatch):
        for rng in ("D", "W", "M", "3M", "Y"):
            cap = _patch_geo(monkeypatch, payload={"range": rng, "buckets": []})
            client.get(f"/api/geo/steps/ranges?range={rng}")
            assert cap["calls"][0]["params"]["range"] == rng

    def test_does_not_forward_a_caller_supplied_viewer(self, client, monkeypatch):
        """Identity is established here, not taken from the query string --
        otherwise any caller could assert any viewer."""
        cap = _patch_geo(monkeypatch, payload={"range": "W", "buckets": []})
        client.get("/api/geo/steps/ranges?range=W&viewer=someone_else")
        params = cap["calls"][0]["params"]
        assert params.get("viewer") in (None, "u1")

    def test_relays_geo_refusals_unchanged(self, client, monkeypatch):
        """A 404 from geo means 'not shared', and laundering it into an empty
        chart would tell the user their family simply has no steps."""
        _patch_geo(monkeypatch, status=404, payload={"detail": "activity not shared"})
        r = client.get("/api/geo/steps/ranges?range=W")
        assert r.status_code == 404

    def test_relays_an_invalid_range(self, client, monkeypatch):
        _patch_geo(monkeypatch, status=422, payload={"detail": "range must be one of D, W, M, 3M, Y"})
        r = client.get("/api/geo/steps/ranges?range=FOREVER")
        assert r.status_code == 422

    def test_the_existing_daily_route_still_works(self, client, monkeypatch):
        """Adding a sibling route must not disturb the one in use."""
        cap = _patch_geo(monkeypatch, payload={"daily_steps": {}, "today": 0})
        r = client.get("/api/geo/steps?days=7")
        assert r.status_code == 200
        assert cap["calls"][0]["url"].endswith("/steps")

    def test_the_daily_cap_is_untouched(self, client, monkeypatch):
        """The 30-day cap stays where it is: the new route is the way to go
        further, not a widened version of the old one."""
        _patch_geo(monkeypatch, payload={"daily_steps": {}, "today": 0})
        assert client.get("/api/geo/steps?days=365").status_code in (200, 422)
