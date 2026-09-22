"""Tests for the geo (family-location) service.

These are unit tests: they mock the Home Assistant state fetch so no live
HA connection is required. They validate endpoint shape and the static
MapLibre client being served at "/".
"""
from unittest.mock import patch

import pytest


@pytest.fixture
def client(monkeypatch):
    import services.geo.main as geo

    # Keep HA as "configured" for index/health, but feed canned states.
    monkeypatch.setattr(geo, "HA_URL", "https://ha.test")
    monkeypatch.setattr(geo, "HA_TOKEN", "tok")

    sample = [
        {
            "entity_id": "person.summers",
            "state": "home",
            "attributes": {
                "friendly_name": "Summers",
                "latitude": 33.1666,
                "longitude": -111.5646,
                "gps_accuracy": 30,
            },
        },
        {
            "entity_id": "zone.home",
            "state": "3",
            "attributes": {
                "friendly_name": "Home",
                "latitude": 33.1667,
                "longitude": -111.5646,
                "radius": 100,
            },
        },
        # No lat/lon -> must be skipped.
        {"entity_id": "person.noloc", "state": "away", "attributes": {}},
    ]

    async def fake_states():
        return sample

    with patch.object(geo, "_ha_get_states", fake_states):
        from fastapi.testclient import TestClient

        yield TestClient(geo.app)


def test_health_reports_configured(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ha_configured"] is True
    assert r.json()["service"] == "geo"


def test_people_geojson(client):
    r = client.get("/people")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    ids = {f["properties"]["entity_id"] for f in body["features"]}
    assert "person.summers" in ids
    assert "person.noloc" not in ids  # skipped: no coords


def test_zones_geojson(client):
    r = client.get("/zones")
    assert r.status_code == 200
    body = r.json()
    ids = {f["properties"]["entity_id"] for f in body["features"]}
    assert "zone.home" in ids


def test_index_serves_client(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "maplibre" in r.text.lower()
    assert "Family Location" in r.text


def test_see_requires_secret(client):
    r = client.post(
        "/people/device_tracker.phone/see",
        json={"latitude": 1.0, "longitude": 2.0},
    )
    assert r.status_code == 403


def test_android_auto_endpoint(client, monkeypatch):
    import services.geo.main as geo

    sample = [
        {
            "entity_id": "binary_sensor.jeremiahs_phone_android_auto_2",
            "state": "off",
            "attributes": {"connection_type": "Disconnected", "friendly_name": "Android Auto"},
            "last_updated": "2026-09-22T00:00:00+00:00",
        },
        {
            "entity_id": "sensor.jeremiahs_phone_detected_activity_2",
            "state": "in_vehicle",
            "attributes": {"friendly_name": "Detected activity"},
            "last_updated": "2026-09-22T00:00:00+00:00",
        },
    ]

    async def fake_states():
        return sample

    monkeypatch.setattr(geo, "_ha_get_states", fake_states)
    r = client.get("/android_auto")
    assert r.status_code == 200
    body = r.json()
    assert body["in_android_auto"] is False
    assert len(body["android_auto"]) == 1
    assert body["android_auto"][0]["connection_type"] == "Disconnected"
    assert body["detected_activity"][0]["state"] == "in_vehicle"


async def test_steps_falls_back_to_ha_when_redis_empty(client, monkeypatch):
    import services.geo.main as geo

    async def fake_ha_states():
        return [
            {
                "entity_id": "sensor.jeremiahs_phone_daily_steps",
                "state": "4321",
                "attributes": {},
                "last_updated": "2026-09-22T12:00:00+00:00",
            }
        ]

    class FakeRedis:
        async def hgetall(self, key):
            return {}

        async def hget(self, key, field):
            return None

        async def hset(self, key, field, value):
            return True

    async def fake_get_redis():
        return FakeRedis()

    monkeypatch.setattr(geo, "_ha_get_states", fake_ha_states)
    monkeypatch.setattr(geo, "get_redis", fake_get_redis)
    r = await geo._steps_from_ha("jeremiahs_phone", days=7)
    assert r
    assert max(r.values()) == 4321


def test_telemetry_at_zone(client):
    r = client.get("/people/summers/telemetry")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["friendly_name"] == "Summers"
    assert data["current_zone"] == "Home"
    assert "Summers is at Home" in data["speech"]


def test_telemetry_moving(client, monkeypatch):
    import time
    import services.geo.main as geo

    # Mock get_points_in_window to return moving points
    t_now = time.time()
    moving_points = [
        {"t": t_now - 60, "lat": 33.2000, "lon": -111.5000, "acc": 5.0, "spd": 15.0, "brg": 45.0, "bat": 85},
        {"t": t_now, "lat": 33.2080, "lon": -111.4900, "acc": 5.0, "spd": 22.0, "brg": 45.0, "bat": 85},
    ]

    async def fake_points(*args, **kwargs):
        return moving_points

    monkeypatch.setattr(geo, "get_points_in_window", fake_points)

    r = client.get("/people/jeremiah/telemetry")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["is_moving"] is True
    assert data["current_speed_mph"] > 30.0
    assert data["top_speed_mph"] >= data["current_speed_mph"]
    assert "traveling at" in data["speech"]


def test_vehicles_empty_by_default(client):
    r = client.get("/vehicles")
    assert r.status_code == 200
    data = r.json()
    assert "vehicles" in data
    assert isinstance(data["vehicles"], list)


def test_vehicle_crud_with_internal_secret_header(client, monkeypatch):
    import json
    import services.geo.main as geo

    # In-memory fake Redis
    store = {}

    class FakeRedis:
        async def hset(self, key, field, value):
            if key not in store:
                store[key] = {}
            store[key][field] = value

        async def hget(self, key, field):
            return store.get(key, {}).get(field)

        async def hgetall(self, key):
            return store.get(key, {})

        async def hdel(self, key, field):
            store.get(key, {}).pop(field, None)

        async def set(self, key, value):
            store[key] = value

        async def get(self, key):
            return store.get(key)

        async def delete(self, key):
            store.pop(key, None)

    async def fake_get_redis():
        return FakeRedis()

    monkeypatch.setattr(geo, "get_redis", fake_get_redis)

    # 1. Unauthorized without header
    r = client.post("/vehicles", json={"id": "truck", "name": "Ford F-150", "mpg": 18.5, "cost_per_gallon": 3.75, "fuel_type": "regular"})
    assert r.status_code == 403

    # 2. Authorized with X-Internal-Secret header
    r = client.post(
        "/vehicles",
        json={"id": "truck", "name": "Ford F-150", "mpg": 18.5, "cost_per_gallon": 3.75, "fuel_type": "regular"},
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    # 3. List vehicles
    r = client.get("/vehicles")
    assert r.status_code == 200
    assert len(r.json()["vehicles"]) == 1
    assert r.json()["vehicles"][0]["name"] == "Ford F-150"

    # 4. Assign vehicle with header
    r = client.post(
        "/vehicles/assign",
        json={"user_id": "jeremiah", "vehicle_id": "truck"},
        headers={"X-Internal-Secret": geo.INTERNAL_SECRET},
    )
    assert r.status_code == 200

    # 5. Check assigned vehicle
    r = client.get("/vehicles/assigned/jeremiah")
    assert r.status_code == 200
    assert r.json()["vehicle_id"] == "truck"
    assert r.json()["vehicle"]["name"] == "Ford F-150"

    # 6. Delete vehicle with header
    r = client.delete("/vehicles/truck", headers={"X-Internal-Secret": geo.INTERNAL_SECRET})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_heavy_duty_vehicle_lookup(client):
    # 1. Options for 2014 Ford F-250 Super Duty
    r = client.get("/vehicle-lookup/options", params={"year": 2014, "make": "Ford", "model": "F-250 Super Duty"})
    assert r.status_code == 200
    items = r.json().get("menuItem", [])
    assert len(items) >= 2
    # Must have 6.7L Diesel option
    diesel_opts = [i for i in items if "6.7L" in i["text"] and "Diesel" in i["text"]]
    assert len(diesel_opts) == 1
    diesel_id = diesel_opts[0]["value"]

    # 2. Detail lookup for that heavy duty diesel option
    r = client.get(f"/vehicle-lookup/{diesel_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["fuelType1"] == "Diesel"
    assert detail["comb08"] == 15.0
    assert detail["make"] == "Ford"
    assert detail["model"] == "F-250 Super Duty"


