"""GET /metrics/ranges — event-metric history, under the same consent gate.

The consent tests are the point of this file. A new read route on geo is
exactly where a sharing check can be quietly skipped, and the failure is
invisible to anyone viewing their own data.
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from services.geo import main as geo_main

TZ = ZoneInfo("America/Phoenix")
TODAY = datetime.now(TZ).replace(hour=12, minute=0, second=0, microsecond=0)


def epoch(days_ago: float, hour: int = 12) -> float:
    return (TODAY - timedelta(days=days_ago)).replace(hour=hour).timestamp()


class FakeRedis:
    """Just enough Redis for these routes."""

    def __init__(self, workouts=None, trips=None):
        self.workouts = workouts or {}
        self.trips = trips or {}

    async def hget(self, *a, **k):
        return None

    async def hgetall(self, *a, **k):
        return {}

    async def get(self, key):
        if key.startswith("geo:workout:"):
            return self.workouts.get(key.split(":", 2)[2])
        if key.startswith("geo:trip:"):
            return self.trips.get(key.split(":", 2)[2])
        return None

    async def zrevrangebyscore(self, key, maxscore, minscore, *a, **k):
        ids = list(self.workouts if "workouts" in key else self.trips)
        return sorted(ids, reverse=True)

    async def zrevrange(self, key, start, stop, *a, **k):
        ids = list(self.workouts if "workouts" in key else self.trips)
        return ids[start:stop + 1]

    async def zrange(self, *a, **k):
        return []

    async def hset(self, *a, **k):
        return 1

    async def hsetnx(self, *a, **k):
        return 1

    async def hdel(self, *a, **k):
        return 1

    async def incr(self, *a, **k):
        return 1

    async def expire(self, *a, **k):
        return True

    async def hlen(self, *a, **k):
        return 0

    async def exists(self, *a, **k):
        return 0


@pytest.fixture
def make_client(monkeypatch):
    def _make(workouts=None, trips=None, allow=True):
        fake = FakeRedis(workouts, trips)

        async def _get_redis():
            return fake

        async def _allow(viewer, target, is_admin):
            if not allow:
                raise geo_main.HTTPException(status_code=404, detail="Not Found")
            return target

        monkeypatch.setattr(geo_main, "get_redis", _get_redis)
        monkeypatch.setattr(geo_main, "_require_may_view", _allow)
        client = TestClient(geo_main.app)
        # geo sits behind an internal-secret middleware; without the header
        # every request 403s before it reaches the route.
        client.headers.update({"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        return client

    return _make


def workout(wid, days_ago, **extra):
    return wid, json.dumps({"id": wid, "start_time": epoch(days_ago), "activity_type": "running", **extra})


def trip(tid, days_ago, miles, activity_type="driving"):
    return tid, json.dumps(
        {"id": tid, "start_time": epoch(days_ago), "distance_miles": miles, "activity_type": activity_type}
    )


class TestWorkoutCount:
    def test_counts_workouts_over_a_month(self, make_client):
        c = make_client(workouts=dict([workout("w1", 0), workout("w2", 1), workout("w3", 3)]))
        r = c.get("/metrics/ranges", params={"metric": "workouts", "range": "M"})
        assert r.status_code == 200
        body = r.json()
        assert body["metric"] == "workouts"
        assert body["total"] == 3
        assert body["active_days"] == 3
        assert body["empty"] is False

    def test_quiet_days_are_zeros_not_gaps(self, make_client):
        c = make_client(workouts=dict([workout("w1", 0)]))
        body = c.get("/metrics/ranges", params={"metric": "workouts", "range": "M"}).json()
        # 30 daily bars, 29 of them quiet -- reported as zero, not "no reading".
        assert len(body["buckets"]) == 30
        assert sum(1 for b in body["buckets"] if b["quiet"]) == 29
        # The step vocabulary must not leak in here.
        assert "days_missing" not in body["buckets"][0]
        assert "has_gaps" not in body

    def test_no_workouts_is_explicitly_empty(self, make_client):
        c = make_client()
        body = c.get("/metrics/ranges", params={"metric": "workouts", "range": "M"}).json()
        assert body["empty"] is True
        assert body["total"] == 0.0
        assert body["best"] is None


class TestDurationAndDistance:
    def test_duration_is_reported_in_minutes(self, make_client):
        c = make_client(workouts=dict([workout("w1", 0, duration_seconds=1800)]))
        body = c.get("/metrics/ranges", params={"metric": "workout_minutes"}).json()
        assert body["unit"] == "minutes"
        assert body["total"] == 30.0

    def test_workout_distance(self, make_client):
        c = make_client(workouts=dict([workout("w1", 0, distance_miles=2.5)]))
        body = c.get("/metrics/ranges", params={"metric": "workout_miles"}).json()
        assert body["unit"] == "miles"
        assert body["total"] == 2.5

    def test_driving_distance_only_counts_driving(self, make_client):
        c = make_client(
            trips=dict([trip("t1", 0, 4.0), trip("t2", 0, 9.0, activity_type="walking")])
        )
        body = c.get("/metrics/ranges", params={"metric": "drive_miles"}).json()
        # The walking trip is not a drive and must not inflate the figure.
        assert body["total"] == 4.0


class TestUntrackedMetrics:
    def test_calories_is_refused_by_name_with_a_reason(self, make_client):
        """calories_burned is a hardcoded None; a card would draw from nothing."""
        c = make_client()
        r = c.get("/metrics/ranges", params={"metric": "calories"})
        assert r.status_code == 422
        assert "calories" in r.json()["detail"]
        assert "None" in r.json()["detail"]

    def test_an_invented_metric_name_is_refused(self, make_client):
        c = make_client()
        r = c.get("/metrics/ranges", params={"metric": "vibes"})
        assert r.status_code == 422
        assert "workouts" in r.json()["detail"]  # names the valid set

    def test_catalog_separates_available_from_unavailable(self, make_client):
        c = make_client()
        body = c.get("/metrics/catalog").json()
        assert "workouts" in body["available"]
        assert "calories" in body["unavailable"]
        for reason in body["unavailable"].values():
            assert reason.strip()


class TestValidation:
    def test_unknown_range_is_422_naming_the_valid_set(self, make_client):
        c = make_client()
        r = c.get("/metrics/ranges", params={"metric": "workouts", "range": "DECADE"})
        assert r.status_code == 422
        assert "D" in r.json()["detail"] and "Y" in r.json()["detail"]

    @pytest.mark.parametrize("rng", ["D", "W", "M", "3M", "Y"])
    def test_every_range_answers(self, make_client, rng):
        c = make_client(workouts=dict([workout("w1", 0)]))
        r = c.get("/metrics/ranges", params={"metric": "workouts", "range": rng})
        assert r.status_code == 200
        assert r.json()["buckets"]


class TestConsent:
    def test_refuses_when_the_viewer_may_not_see_them(self, make_client):
        c = make_client(workouts=dict([workout("w1", 0)]), allow=False)
        r = c.get(
            "/metrics/ranges",
            params={"metric": "workouts", "viewer": "jeremiah", "user_id": "michele"},
        )
        assert r.status_code == 404

    def test_gate_runs_before_any_data_is_read(self, make_client):
        """A refusal must not depend on whether the user happens to have data."""
        c = make_client(allow=False)
        r = c.get("/metrics/ranges", params={"metric": "workouts", "viewer": "jeremiah", "user_id": "michele"})
        assert r.status_code == 404

    def test_a_new_route_does_not_become_a_consent_bypass(self, monkeypatch):
        """Pins that the route actually calls the shared authority."""
        called = []
        real = geo_main._require_may_view

        async def spy(viewer, target, is_admin):
            called.append((viewer, target, is_admin))
            return await real(viewer, target, is_admin)

        monkeypatch.setattr(geo_main, "_require_may_view", spy)

        async def _get_redis():
            return FakeRedis()

        monkeypatch.setattr(geo_main, "get_redis", _get_redis)
        client = TestClient(geo_main.app)
        client.headers.update({"X-Internal-Secret": geo_main.INTERNAL_SECRET})
        client.get("/metrics/ranges", params={"metric": "workouts", "viewer": "jeremiah", "user_id": "michele"})
        assert called == [("jeremiah", "michele", None)]

    def test_every_metric_shares_the_one_gate(self, make_client):
        """One route, many metrics -- all of them gated, none of them opting out."""
        c = make_client(workouts=dict([workout("w1", 0)]), allow=False)
        for metric in ("workouts", "workout_minutes", "workout_miles", "drive_miles"):
            r = c.get(
                "/metrics/ranges",
                params={"metric": metric, "viewer": "jeremiah", "user_id": "michele"},
            )
            assert r.status_code == 404, metric
