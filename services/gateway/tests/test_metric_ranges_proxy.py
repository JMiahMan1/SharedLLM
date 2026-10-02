"""Gateway proxy for the metric-history routes.

Same reasoning as test_step_ranges_proxy: the client cannot reach geo directly,
so a route that does not exist here does not exist for the app. And because a
geo 404 means "not shared", the refusal has to be relayed rather than laundered
into an empty chart -- which would tell a user their family has no workouts.
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
    """Patches ``_resolve_strict_identity``: ``resolve_identity`` would resolve
    any junk string to the default admin, so tests would pass for the wrong
    reason."""

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


PAYLOAD = {"metric": "workouts", "range": "M", "total": 3.0, "buckets": [], "empty": False}


class TestMetricRangeProxy:
    def test_forwards_the_metric(self, client, monkeypatch):
        cap = _patch_geo(monkeypatch, payload=PAYLOAD)
        r = client.get("/api/geo/metrics/ranges?metric=workouts&range=M")
        assert r.status_code == 200
        assert cap["calls"][0]["url"].endswith("/metrics/ranges")
        assert cap["calls"][0]["params"]["metric"] == "workouts"
        assert cap["calls"][0]["params"]["range"] == "M"

    @pytest.mark.parametrize(
        "metric", ["workouts", "workout_minutes", "workout_miles", "drive_miles"]
    )
    def test_forwards_every_available_metric(self, client, monkeypatch, metric):
        cap = _patch_geo(monkeypatch, payload=PAYLOAD)
        client.get(f"/api/geo/metrics/ranges?metric={metric}")
        assert cap["calls"][0]["params"]["metric"] == metric

    def test_relays_the_payload_unchanged(self, client, monkeypatch):
        _patch_geo(monkeypatch, payload=PAYLOAD)
        body = client.get("/api/geo/metrics/ranges?metric=workouts").json()
        assert body["total"] == 3.0
        assert body["empty"] is False

    def test_does_not_forward_a_caller_supplied_viewer(self, client, monkeypatch):
        """Identity is established here, not read from the query string."""
        cap = _patch_geo(monkeypatch, payload=PAYLOAD)
        client.get("/api/geo/metrics/ranges?metric=workouts&viewer=someone_else")
        assert cap["calls"][0]["params"].get("viewer") in (None, "u1")

    def test_relays_geo_refusals_unchanged(self, client, monkeypatch):
        """A 404 is 'not shared'; an empty chart would be a lie about the data."""
        _patch_geo(monkeypatch, status=404, payload={"detail": "activity not shared"})
        r = client.get("/api/geo/metrics/ranges?metric=workouts")
        assert r.status_code == 404

    def test_relays_the_untracked_metric_refusal(self, client, monkeypatch):
        """geo answers 422 for calories, with the reason; it must survive."""
        _patch_geo(monkeypatch, status=422, payload={"detail": "calories is not tracked: never recorded"})
        r = client.get("/api/geo/metrics/ranges?metric=calories")
        assert r.status_code == 422
        assert "calories" in r.json()["detail"]

    def test_sends_the_internal_secret(self, client, monkeypatch):
        cap = _patch_geo(monkeypatch, payload=PAYLOAD)
        client.get("/api/geo/metrics/ranges?metric=workouts")
        assert cap["calls"][0]["headers"].get("X-Internal-Secret") == gateway_main.INTERNAL_SECRET


class TestCatalogProxy:
    def test_forwards_to_the_catalog_route(self, client, monkeypatch):
        cap = _patch_geo(monkeypatch, payload={"available": ["workouts"], "unavailable": {"calories": "x"}})
        r = client.get("/api/geo/metrics/catalog")
        assert r.status_code == 200
        assert cap["calls"][0]["url"].endswith("/metrics/catalog")
        assert "workouts" in r.json()["available"]

    def test_catalog_names_nobody(self, client, monkeypatch):
        """It reports what is recorded, not who recorded it."""
        cap = _patch_geo(monkeypatch, payload={"available": ["workouts"], "unavailable": {}})
        client.get("/api/geo/metrics/catalog")
        assert cap["calls"][0]["params"] is None


class TestRouteOrdering:
    def test_the_literal_catalog_path_is_not_captured_as_a_metric(self, client, monkeypatch):
        cap = _patch_geo(monkeypatch, payload={"available": []})
        client.get("/api/geo/metrics/catalog")
        assert len(cap["calls"]) == 1
        assert cap["calls"][0]["url"].endswith("/metrics/catalog")

    def test_the_ranges_route_is_reachable_alongside_it(self, client, monkeypatch):
        cap = _patch_geo(monkeypatch, payload=PAYLOAD)
        client.get("/api/geo/metrics/ranges?metric=workouts")
        assert cap["calls"][0]["url"].endswith("/metrics/ranges")
