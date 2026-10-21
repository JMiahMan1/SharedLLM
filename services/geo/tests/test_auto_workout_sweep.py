"""Tests for the background GPS workout detector."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo
from services.config import INTERNAL_SECRET
from services.geo.tests.test_trips import FakeRedis


def trail(mph: float, minutes: int, *, start: float = 1_700_000_000.0, spacing: int = 30) -> list[dict]:
    points = []
    step_m = mph * 0.44704 * spacing
    lat = 33.1667
    for index in range(int(minutes * 60 / spacing) + 1):
        points.append({
            "t": start + index * spacing,
            "lat": lat + index * step_m / 111_320.0,
            "lon": -111.5646,
            "spd": mph * 0.44704,
            "acc": 8,
        })
    return points


def recent_trail(mph: float, minutes: int) -> list[dict]:
    return trail(mph, minutes, start=time.time() - 3600)


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()

    async def _get_r():
        return r

    monkeypatch.setattr(geo, "get_redis", _get_r)
    return r


@pytest.fixture
def client(fake_redis):
    return TestClient(geo.app, headers={"X-Internal-Secret": INTERNAL_SECRET})


def _store_points(fake_redis, user: str, points: list[dict]):
    for point in points:
        fake_redis.zsets.setdefault(f"geo:history:{user}", []).append(
            (json.dumps(point), point["t"])
        )


@pytest.mark.asyncio
async def test_the_sweep_saves_one_auto_workout(fake_redis):
    points = recent_trail(3.0, 12)
    _store_points(fake_redis, "jeremiah", points)

    result = await geo._auto_workout_sweep(fake_redis, now=points[-1]["t"] + 10)

    assert result["created"] == 1
    workout_id = f"auto_workout_jeremiah_{int(points[0]['t'])}"
    workout = json.loads(await fake_redis.get(f"geo:workout:{workout_id}"))
    assert workout["activity_type"] == "walking"
    assert workout["auto_detected"] is True
    assert workout["confirmed"] is False
    assert workout["source"] == "auto_gps"
    assert workout["steps"] and workout["steps_source"] == "gps_estimate"
    assert await fake_redis.zcard("geo:workouts:user:jeremiah") == 1
    assert await fake_redis.zcard("geo:workouts:all") == 1


@pytest.mark.asyncio
async def test_the_sweep_does_not_duplicate_an_existing_auto_workout(fake_redis):
    points = recent_trail(3.0, 12)
    _store_points(fake_redis, "jeremiah", points)

    assert (await geo._auto_workout_sweep(fake_redis, now=points[-1]["t"] + 10))["created"] == 1
    assert (await geo._auto_workout_sweep(fake_redis, now=points[-1]["t"] + 20))["created"] == 0


@pytest.mark.asyncio
async def test_an_active_workout_or_trip_pauses_the_sweep(fake_redis):
    points = recent_trail(3.0, 12)
    _store_points(fake_redis, "jeremiah", points)
    await fake_redis.set("geo:active_trip:jeremiah", "{}")

    result = await geo._auto_workout_sweep(fake_redis, now=points[-1]["t"] + 10)

    assert result["created"] == 0
    assert result["skipped"] == 1


@pytest.mark.asyncio
async def test_a_trip_hides_the_same_walk(fake_redis):
    points = recent_trail(3.0, 12)
    _store_points(fake_redis, "jeremiah", points)
    trip = {
        "id": "trip_jeremiah_1",
        "user_id": "jeremiah",
        "start_time": points[0]["t"],
        "end_time": points[-1]["t"],
        "status": "completed",
    }
    await fake_redis.set("geo:trip:trip_jeremiah_1", json.dumps(trip))
    await fake_redis.zadd("geo:trips:user:jeremiah", {"trip_jeremiah_1": points[0]["t"]})

    result = await geo._auto_workout_sweep(fake_redis, now=points[-1]["t"] + 10)

    assert result["created"] == 0


@pytest.mark.asyncio
async def test_dismiss_removes_the_auto_workout_and_prevents_redetection(fake_redis, client):
    points = recent_trail(3.0, 12)
    _store_points(fake_redis, "jeremiah", points)
    workout_id = f"auto_workout_jeremiah_{int(points[0]['t'])}"
    workout = {
        "id": workout_id,
        "user_id": "jeremiah",
        "activity_type": "walking",
        "start_time": points[0]["t"],
        "end_time": points[-1]["t"],
        "status": "completed",
        "auto_detected": True,
    }
    fake_redis.data[f"geo:workout:{workout_id}"] = json.dumps(workout)
    fake_redis.zsets["geo:workouts:user:jeremiah"] = [(workout_id, points[0]["t"])]
    fake_redis.zsets["geo:workouts:all"] = [(workout_id, points[0]["t"])]

    resp = client.post(f"/workouts/{workout_id}/dismiss", headers={"X-User-Id": "jeremiah"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "dismissed"
    assert await fake_redis.get(f"geo:workout:{workout_id}") is None
    assert await fake_redis.zcard("geo:workouts:user:jeremiah") == 0
    assert await fake_redis.zcard("geo:workouts:all") == 0
    assert await fake_redis.zcard("geo:auto_workout_dismissed:jeremiah") == 1
    again = await geo._auto_workout_sweep(fake_redis, now=points[-1]["t"] + 10)
    assert again["created"] == 0


def test_a_manual_workout_cannot_be_dismissed(fake_redis, client):
    workout = {
        "id": "workout_jeremiah_1",
        "user_id": "jeremiah",
        "activity_type": "walking",
        "start_time": time.time() - 600,
        "end_time": time.time(),
        "status": "completed",
    }
    fake_redis.data["geo:workout:workout_jeremiah_1"] = json.dumps(workout)

    resp = client.post("/workouts/workout_jeremiah_1/dismiss", headers={"X-User-Id": "jeremiah"})

    assert resp.status_code == 400
