"""`GET /events` — a dated timeline of things that happened.

A module test cannot tell you this route is reachable, that it is
consent-gated, or that it degrades to an empty timeline rather than an error.
Those are all here.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from services.geo import main as geo_main


def _patch_redis(monkeypatch, workouts=(), trips=(), ledger=None):
    """A minimal async Redis stand-in holding only what this route reads."""
    ledger = ledger or {}

    class _R:
        async def zrevrangebyscore(self, key, _hi, _lo):
            if key.startswith("geo:workouts:user:"):
                return [f"w{i}" for i in range(len(workouts))]
            if key.startswith("geo:trips:user:"):
                return [f"t{i}" for i in range(len(trips))]
            return []

        async def get(self, key):
            if key.startswith("geo:workout:"):
                idx = int(key.rsplit(":", 1)[1][1:])
                return json.dumps(workouts[idx]) if idx < len(workouts) else None
            if key.startswith("geo:trip:"):
                idx = int(key.rsplit(":", 1)[1][1:])
                return json.dumps(trips[idx]) if idx < len(trips) else None
            if key.startswith("geo:points:"):
                return json.dumps(ledger) if ledger else None
            return None

    async def fake_redis():
        return _R()

    monkeypatch.setattr(geo_main, "get_redis", fake_redis)


@pytest.fixture
def client(monkeypatch):
    c = TestClient(geo_main.app)
    # geo sits behind an internal-secret middleware; without the header every
    # request 403s before it reaches the route.
    c.headers.update({"X-Internal-Secret": geo_main.INTERNAL_SECRET})
    return c


def _workout(start, **extra):
    return {"activity_type": "run", "start_time": start, "duration_seconds": 1800, **extra}


class TestEventsRoute:
    def test_is_not_a_404(self, client, monkeypatch):
        _patch_redis(monkeypatch)
        r = client.get("/events?viewer=jeremiah&user_id=jeremiah")
        assert r.status_code == 200

    def test_reports_an_empty_timeline_rather_than_an_error(self, client, monkeypatch):
        _patch_redis(monkeypatch)
        body = client.get("/events?viewer=jeremiah&user_id=jeremiah").json()
        assert body["empty"] is True
        assert body["total_events"] == 0
        # An empty list plus an explicit flag, so the client can say
        # "nothing recorded yet" instead of rendering a broken-looking list.
        assert body["groups"] == []
        assert body["day_count"] == 0

    def test_returns_workouts_as_events(self, client, monkeypatch):
        import time

        _patch_redis(monkeypatch, workouts=[_workout(time.time() - 3600)])
        body = client.get("/events?viewer=jeremiah&user_id=jeremiah").json()
        assert body["empty"] is False
        assert body["total_events"] == 1
        assert body["groups"][0]["events"][0]["kind"] == "workout"

    def test_resolves_the_viewer_for_a_shared_view(self, client, monkeypatch):
        _patch_redis(monkeypatch)
        # _require_may_view normally calls identity to ask whether the viewer may
        # see the target, which cannot resolve in a unit test. Patched to the
        # allow so the route is exercised; the consent behaviour itself is
        # asserted by TestEventsConsent and by the sibling route suites.
        async def _allow(viewer, target, is_admin):
            return target

        monkeypatch.setattr(geo_main, "_require_may_view", _allow)
        body = client.get("/events?viewer=jeremiah&user_id=michele").json()
        assert body["user_id"] == "michele"

    def test_echoes_the_requested_window(self, client, monkeypatch):
        # The requested window and the number of day groups returned are
        # different facts and must not share a key -- one silently overwrote
        # the other.
        _patch_redis(monkeypatch)
        body = client.get("/events?viewer=jeremiah&user_id=jeremiah&days=7").json()
        assert body["window_days"] == 7
        assert "days" not in body

    def test_survives_a_broken_event_blob(self, client, monkeypatch):
        """A corrupt record must not take the whole timeline down."""

        class _Broken:
            async def zrevrangebyscore(self, key, _hi, _lo):
                return ["w0"] if key.startswith("geo:workouts:user:") else []

            async def get(self, key):
                return "{not json"

        async def fake_redis():
            return _Broken()

        monkeypatch.setattr(geo_main, "get_redis", fake_redis)
        r = client.get("/events?viewer=jeremiah&user_id=jeremiah")
        assert r.status_code == 200
        assert r.json()["total_events"] == 0

    def test_degrades_when_redis_is_unavailable(self, client, monkeypatch):
        async def fake_redis():
            return None

        monkeypatch.setattr(geo_main, "get_redis", fake_redis)
        r = client.get("/events?viewer=jeremiah&user_id=jeremiah")
        assert r.status_code == 200
        assert r.json()["empty"] is True

    @pytest.mark.parametrize("q", ["days=0", "days=400", "limit=0", "limit=500"])
    def test_rejects_an_out_of_range_window(self, client, monkeypatch, q):
        _patch_redis(monkeypatch)
        assert client.get(f"/events?viewer=jeremiah&user_id=jeremiah&{q}").status_code == 422


class TestEventsConsent:
    """A new route must not become a way around the sharing rules."""

    def test_opted_out_user_is_a_404(self, client, monkeypatch):
        _patch_redis(monkeypatch)
        r = client.get("/events?viewer=jeremiah&user_id=stranger")
        assert r.status_code == 404

    def test_absent_viewer_is_treated_as_internal(self, client, monkeypatch):
        """Same rule as the sibling routes: no viewer means a trusted internal
        caller, which the internal-secret middleware is what actually guards."""
        _patch_redis(monkeypatch)
        assert client.get("/events?user_id=anyone").status_code == 200

    def test_known_viewer_is_still_filtered(self, client, monkeypatch):
        """The permissive internal path must not leak to a named viewer."""
        _patch_redis(monkeypatch)
        assert client.get("/events?viewer=jeremiah&user_id=nobody").status_code == 404


def _trip(start, **extra):
    return {
        "activity_type": "driving",
        "start_time": start,
        "duration_seconds": 900,
        "distance_miles": 3.1,
        **extra,
    }


class TestEventsDomainRoute:
    """Health and Wander are separate views of the same stored events."""

    def _kinds(self, client, **params):
        r = client.get("/events", params={"viewer": "jeremiah", "user_id": "jeremiah", **params})
        assert r.status_code == 200, r.text
        return {e["kind"] for g in r.json()["groups"] for e in g["events"]}

    def test_health_omits_drives_even_when_there_are_some(
        self, client, monkeypatch
    ):
        _patch_redis(
            monkeypatch,
            workouts=[_workout(time.time() - 3600)],
            trips=[_trip(time.time() - 7200)],
        )
        assert self._kinds(client) == {"workout"}

    def test_wander_returns_the_drives(self, client, monkeypatch):
        _patch_redis(
            monkeypatch,
            workouts=[_workout(time.time() - 3600)],
            trips=[_trip(time.time() - 7200)],
        )
        assert self._kinds(client, domain="wander") == {"drive"}

    def test_the_default_domain_is_health_not_the_old_blend(self, client, monkeypatch):
        # Regression: drives used to appear under Health by default.
        _patch_redis(monkeypatch, trips=[_trip(time.time() - 7200)])
        assert "drive" not in self._kinds(client)

    def test_echoes_the_domain_it_served(self, client, monkeypatch):
        _patch_redis(monkeypatch, trips=[_trip(time.time() - 7200)])
        r = client.get(
            "/events", params={"viewer": "jeremiah", "user_id": "jeremiah", "domain": "wander"}
        )
        assert r.json()["domain"] == "wander"

    def test_an_unknown_domain_is_422_naming_the_valid_set(self, client, monkeypatch):
        _patch_redis(monkeypatch)
        r = client.get(
            "/events", params={"viewer": "jeremiah", "user_id": "jeremiah", "domain": "heath"}
        )
        assert r.status_code == 422
        assert "health" in r.json()["detail"] and "wander" in r.json()["detail"]

    def test_the_domain_is_validated_before_any_redis_work(self, client, monkeypatch):
        # A bad request must not depend on whether the user has data.
        async def boom():
            raise AssertionError("Redis was touched for an invalid domain")

        monkeypatch.setattr(geo_main, "get_redis", boom)
        r = client.get(
            "/events", params={"viewer": "jeremiah", "user_id": "jeremiah", "domain": "nope"}
        )
        assert r.status_code == 422
