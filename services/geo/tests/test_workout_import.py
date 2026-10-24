"""Workouts imported from Health Connect (`POST /workouts/import`).

A phone re-reads an overlapping window on every sync, so the route must be
idempotent per external id; a walk geo already recorded from GPS must not be
counted a second time from a watch; and an activity geo cannot name is
refused rather than filed as something else.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo
from services.config import INTERNAL_SECRET
from services.geo import workout_import


class _FakeRedis:
    def __init__(self):
        self.store: dict = {}
        self.zsets: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def zadd(self, name, mapping):
        entries = dict(self.zsets.get(name, {}))
        entries.update(mapping)
        self.zsets[name] = entries

    async def zrevrange(self, name, start, stop):
        ordered = sorted(self.zsets.get(name, {}).items(), key=lambda kv: kv[1], reverse=True)
        return [key for key, _ in ordered][start : stop + 1]


async def _ready(redis):
    return redis


@pytest.fixture
def setup(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr(geo, "get_redis", lambda: _ready(redis))
    client = TestClient(geo.app, headers={"X-Internal-Secret": INTERNAL_SECRET, "X-User-Id": "kate"})
    return client, redis


def _session(external_id="hc:exercise:1", start=None, minutes=30, **extra):
    start = start if start is not None else time.time() - 3 * 3600
    return {
        "external_id": external_id,
        "activity_type": "walking",
        "start_time": start,
        "end_time": start + minutes * 60,
        **extra,
    }


def test_a_session_is_saved_once_and_replays_are_duplicates(setup):
    client, redis = setup
    body = {"source": "health_connect", "workouts": [_session(distance_meters=3218.688, title="Evening walk")]}

    first = client.post("/workouts/import", json=body)
    assert first.status_code == 200
    assert first.json()["imported"] == 1
    saved = first.json()["workouts"][0]
    assert saved["source"] == "health_connect"
    assert saved["distance_miles"] == 2.0
    assert saved["duration_seconds"] == 1800
    # The pedometer owns steps; an import never adds a second count.
    assert saved["steps"] is None
    assert saved["notes"] == "Evening walk"
    assert saved["id"] in redis.zsets["geo:workouts:user:kate"]

    again = client.post("/workouts/import", json=body)
    assert again.json() == {"imported": 0, "duplicates": 1, "skipped": [], "workouts": []}


def test_a_walk_geo_already_has_is_not_counted_twice(setup):
    client, redis = setup
    start = time.time() - 5 * 3600
    gps = {"id": "workout_kate_1", "user_id": "kate", "start_time": start, "end_time": start + 40 * 60}
    redis.store["geo:workout:workout_kate_1"] = json.dumps(gps)
    redis.zsets["geo:workouts:user:kate"] = {"workout_kate_1": start}

    resp = client.post(
        "/workouts/import",
        json={"source": "health_connect", "workouts": [_session(start=start + 5 * 60, minutes=30)]},
    )
    body = resp.json()
    assert body["imported"] == 0
    assert body["skipped"] == [{"external_id": "hc:exercise:1", "reason": "overlaps", "workout_id": "workout_kate_1"}]


def test_unknown_types_and_bad_times_are_refused_with_a_reason(setup):
    client, _ = setup
    swim = _session("hc:exercise:swim", activity_type="swimming")
    backwards = _session("hc:exercise:back")
    backwards["end_time"] = backwards["start_time"] - 60
    drive = _session("hc:exercise:drive", activity_type="driving")
    resp = client.post("/workouts/import", json={"source": "health_connect", "workouts": [swim, backwards, drive]})
    reasons = {row["external_id"]: row["reason"] for row in resp.json()["skipped"]}
    assert reasons == {
        "hc:exercise:swim": "unsupported_type",
        "hc:exercise:back": "invalid_time",
        "hc:exercise:drive": "unsupported_type",
    }


def test_source_must_name_a_device(setup):
    client, _ = setup
    assert client.post("/workouts/import", json={"source": "manual", "workouts": [_session()]}).status_code == 422
    assert client.post("/workouts/import", json={"source": "Bad Source", "workouts": [_session()]}).status_code == 422


def test_it_requires_the_internal_secret():
    bare = TestClient(geo.app)
    resp = bare.post("/workouts/import", json={"source": "health_connect", "workouts": [_session()]})
    assert resp.status_code == 403


def test_only_another_sources_workout_counts_as_a_repeat():
    walk = {"id": "w", "source": "health_connect", "start_time": 1000.0, "end_time": 2800.0}
    assert workout_import.duplicate_of(1500.0, 2500.0, "health_connect", [walk]) is None
    gps = {"id": "g", "start_time": 1000.0, "end_time": 2800.0}
    assert workout_import.duplicate_of(1500.0, 2500.0, "health_connect", [gps]) is gps
    assert workout_import.duplicate_of(5000.0, 6000.0, "health_connect", [gps]) is None
