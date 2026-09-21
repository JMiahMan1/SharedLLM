"""Tests for Trip recording, shared trip grouping, and trip editing in the geo service."""
import json
import time
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.zsets = {}
        self.hashes = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)

    async def hget(self, hname, key):
        return self.hashes.get(hname, {}).get(key)

    async def hset(self, hname, key, value):
        if hname not in self.hashes:
            self.hashes[hname] = {}
        self.hashes[hname][key] = value

    async def hgetall(self, hname):
        return self.hashes.get(hname, {})

    async def zadd(self, zname, mapping):
        if zname not in self.zsets:
            self.zsets[zname] = []
        for val, score in mapping.items():
            self.zsets[zname].append((val, score))

    async def zcard(self, zname):
        return len(self.zsets.get(zname, []))

    async def zrevrange(self, zname, start, stop):
        items = self.zsets.get(zname, [])
        # sorted by score desc
        sorted_items = sorted(items, key=lambda x: x[1], reverse=True)
        vals = [item[0] for item in sorted_items]
        return vals[start : stop + 1] if stop >= 0 else vals[start:]

    async def keys(self, pattern):
        return [k for k in self.data.keys() if "active_trip" in k]


@pytest.fixture
def fake_redis(monkeypatch):
    r = FakeRedis()
    async def _get_r():
        return r
    monkeypatch.setattr(geo, "get_redis", _get_r)
    return r


@pytest.fixture
def client(fake_redis):
    return TestClient(geo.app)


def test_shared_trips_grouping():
    """Verify trips occurring at the same time and place are grouped as shared trips."""
    now = time.time()
    trips = [
        {
            "id": "t1",
            "user_id": "jeremiah",
            "user_name": "Jeremiah",
            "start_time": now,
            "end_time": now + 1800,
            "start_location": {"name": "Home", "latitude": 33.1667, "longitude": -111.5646},
            "end_location": {"name": "Mall", "latitude": 33.2500, "longitude": -111.6300},
            "distance_miles": 12.0,
            "vehicle_name": "2011 Ford F-250 Super Duty",
        },
        {
            "id": "t2",
            "user_id": "michele",
            "user_name": "Michele",
            "start_time": now + 60, # 1 minute later
            "end_time": now + 1820,
            "start_location": {"name": "Home", "latitude": 33.1667, "longitude": -111.5646},
            "end_location": {"name": "Mall", "latitude": 33.2500, "longitude": -111.6300},
            "distance_miles": 12.0,
            "vehicle_name": "2012 Chevrolet Equinox FWD",
        },
        {
            "id": "t3",
            "user_id": "jeremiah",
            "user_name": "Jeremiah",
            "start_time": now - 36000,
            "end_time": now - 34000,
            "start_location": {"name": "Work", "latitude": 32.7800, "longitude": -96.8000},
            "end_location": {"name": "Airport", "latitude": 32.8900, "longitude": -97.0400},
            "distance_miles": 18.5,
            "vehicle_name": "2011 Ford F-250 Super Duty",
        }
    ]

    grouped = geo.group_shared_trips(trips)
    assert grouped[0]["is_shared"] is True
    assert grouped[0]["shared_with"][0]["user_id"] == "michele"
    assert grouped[1]["is_shared"] is True
    assert grouped[1]["shared_with"][0]["user_id"] == "jeremiah"
    assert grouped[0]["shared_group_id"] == grouped[1]["shared_group_id"]

    # t3 was a solo trip in a completely different time/location
    assert grouped[2]["is_shared"] is False


def test_get_trips_seed_and_filter(client, fake_redis):
    """Verify trips are retrieved per user and over all with initial seed data."""
    # Getting all trips
    r = client.get("/trips")
    assert r.status_code == 200
    data = r.json()
    assert data["total_trips"] >= 2
    users = {t["user_id"] for t in data["trips"]}
    assert "jeremiah" in users
    assert "michele" in users

    # Filter per login (Jeremiah)
    r_j = client.get("/trips?user_id=jeremiah")
    assert r_j.status_code == 200
    for t in r_j.json()["trips"]:
        assert t["user_id"] == "jeremiah"

    # Filter per login (Michele)
    r_m = client.get("/trips?user_id=michele")
    assert r_m.status_code == 200
    for t in r_m.json()["trips"]:
        assert t["user_id"] == "michele"


def test_update_trip_by_owner_allowed(client, fake_redis):
    """Verify owner can update vehicle, MPG, and fuel price, and cost is recalculated."""
    client.get("/trips") # triggers initial seed
    trips = client.get("/trips?user_id=jeremiah").json()["trips"]
    target_trip = trips[0]
    trip_id = target_trip["id"]
    orig_miles = target_trip["distance_miles"]
    orig_start = target_trip["start_location"]

    update_payload = {
        "vehicle_id": "custom_truck",
        "vehicle_name": "Modified Ford F-250",
        "fuel_type": "diesel",
        "mpg": 18.0,
        "cost_per_gallon": 4.50,
    }

    resp = client.patch(
        f"/trips/{trip_id}",
        json=update_payload,
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET, "X-User-Id": "jeremiah"},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["vehicle_name"] == "Modified Ford F-250"
    assert updated["mpg"] == 18.0
    assert updated["cost_per_gallon"] == 4.50
    # Location and mileage remain untouched
    assert updated["distance_miles"] == orig_miles
    assert updated["start_location"] == orig_start
    # Cost is recomputed: (miles / 18.0) * 4.50
    expected_gal = round(orig_miles / 18.0, 2)
    expected_cost = round(expected_gal * 4.50, 2)
    assert updated["fuel_used_gal"] == expected_gal
    assert updated["trip_cost_usd"] == expected_cost


def test_update_trip_by_other_user_forbidden(client, fake_redis):
    """Verify updating a trip by another user is strictly rejected with 403."""
    client.get("/trips") # triggers initial seed
    trips = client.get("/trips?user_id=jeremiah").json()["trips"]
    target_trip = trips[0]
    trip_id = target_trip["id"]

    resp = client.patch(
        f"/trips/{trip_id}",
        json={"vehicle_name": "Hacked Vehicle"},
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET, "X-User-Id": "michele"},
    )
    assert resp.status_code == 403
    assert "Only the user who took this trip" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_trip_detection_over_10_mph(fake_redis):
    """Verify trip starts when speed over 10 MPH is reached, using default vehicle."""
    # Configure user's vehicle
    await fake_redis.hset("geo:vehicles", "my_truck", json.dumps({
        "id": "my_truck",
        "name": "2014 Ford F-250",
        "mpg": 16.0,
        "cost_per_gallon": 4.10,
        "fuel_type": "diesel"
    }))
    await fake_redis.set("geo:user_vehicle:jeremiah", "my_truck")

    now = time.time()
    # Speed of 6.0 m/s = ~13.4 MPH (> 10 MPH threshold)
    await geo.process_trip_point(
        user_id="jeremiah",
        lat=33.1667,
        lon=-111.5646,
        speed_mps=6.0,
        timestamp=now
    )

    active_raw = await fake_redis.get("geo:active_trip:jeremiah")
    assert active_raw is not None
    active = json.loads(active_raw)
    assert active["status"] == "in_progress"
    assert active["vehicle_name"] == "2014 Ford F-250"
    assert active["fuel_type"] == "diesel"
    assert active["mpg"] == 16.0
    assert active["top_speed_mph"] >= 13.0
