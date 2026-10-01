"""Nearby place suggestions and editable trip location names.

Covers POST-safe flows: suggestions degrade gracefully when Overpass is down,
and PATCH accepts display-name/address edits while coordinates stay locked.
"""
import time
from unittest.mock import AsyncMock


import json

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo
from services.config import INTERNAL_SECRET


class FakeRedis:
    def __init__(self):
        self.strings: dict[str, str] = {}
        self.zsets: dict[str, list[tuple[str, float]]] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def get(self, key):
        return self.strings.get(key)

    async def set(self, key, value, ex=None):
        self.strings[key] = str(value)
        return True

    async def delete(self, key):
        self.strings.pop(key, None)

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def hset(self, key, field=None, value=None, mapping=None):
        bucket = self.hashes.setdefault(key, {})
        if mapping:
            for k, v in mapping.items():
                bucket[str(k)] = str(v)
        if field is not None:
            bucket[str(field)] = str(value)
        return 1

    async def zadd(self, zname, mapping):
        items = self.zsets.setdefault(zname, [])
        for val, score in mapping.items():
            self.zsets[zname] = [(v, s) for v, s in items if v != val]
            self.zsets[zname].append((val, score))
        return 1

    async def zcard(self, zname):
        return len(self.zsets.get(zname, []))

    async def zrevrange(self, zname, start, stop):
        vals = [v for v, _ in sorted(self.zsets.get(zname, []), key=lambda x: x[1], reverse=True)]
        return vals[start : stop + 1] if stop >= 0 else vals[start:]

    async def zrangebyscore(self, zname, lo, hi):
        return [v for v, s in self.zsets.get(zname, []) if float(lo) <= s <= float(hi)]

    async def zrevrangebyscore(self, zname, hi, lo):
        return [v for v, s in self.zsets.get(zname, []) if float(lo) <= s <= float(hi)]


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


HEADERS = {"X-Internal-Secret": geo.INTERNAL_SECRET, "X-User-Id": "jeremiah"}


def _insert_trip(r: FakeRedis, trip_id: str, user_id: str = "jeremiah") -> dict:
    now = time.time()
    trip = {
        "id": trip_id,
        "user_id": user_id,
        "user_name": "Jeremiah",
        "start_time": now - 3600,
        "end_time": now - 1800,
        "duration_seconds": 1800,
        "distance_miles": 12.0,
        "top_speed_mph": 55.0,
        "start_location": {"name": "Home", "latitude": 33.1667, "longitude": -111.5646},
        "end_location": {"name": "Mall", "latitude": 33.25, "longitude": -111.63},
        "fuel_type": "gasoline",
        "mpg": 24.0,
        "cost_per_gallon": 3.65,
        "status": "completed",
    }
    r.strings[f"geo:trip:{trip_id}"] = json.dumps(trip)
    return trip


def _mock_place_sources(monkeypatch, *, pois=None):
    monkeypatch.setattr(
        geo,
        "_resolve_place_details_cached",
        AsyncMock(return_value={"name": "Home", "address": "1 Main St, Phoenix, AZ", "source": "ha_zone"}),
    )
    monkeypatch.setattr(
        geo,
        "_nominatim_reverse_details",
        AsyncMock(
            return_value={
                "display_name": "1 Main St, Phoenix, Maricopa County, Arizona",
                "address": {"house_number": "1", "road": "Main St", "city": "Phoenix"},
            }
        ),
    )
    monkeypatch.setattr(
        geo,
        "_ha_zones_containing",
        AsyncMock(return_value=[{"name": "Home", "kind": "zone", "latitude": 33.1667, "longitude": -111.5646, "distance_m": 0}]),
    )
    monkeypatch.setattr(
        geo,
        "_overpass_nearby_pois",
        AsyncMock(
            return_value=pois
            if pois is not None
            else [
                {"name": "Whole Foods Market", "kind": "shop", "latitude": 33.167, "longitude": -111.564, "distance_m": 60},
                {"name": "Pizzeria Bianco", "kind": "restaurant", "latitude": 33.168, "longitude": -111.565, "distance_m": 120},
            ]
        ),
    )


def test_suggestions_include_zones_pois_and_addresses(client, fake_redis, monkeypatch):
    _mock_place_sources(monkeypatch)
    resp = client.get("/locations/suggestions?lat=33.1667&lon=-111.5646", headers=HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["current"]["name"] == "Home"
    names = [c["name"] for c in data["candidates"]]
    kinds = {c["kind"] for c in data["candidates"]}
    assert "Whole Foods Market" in names
    assert "zone" in kinds and "shop" in kinds and "address" in kinds
    # Address components come from Nominatim (road + city)
    assert any("Main St" in n for n in names)
    assert "Phoenix" in names or any("Phoenix" in n for n in names)
    # Zones are nearest-first, and names are deduped
    assert len(names) == len(set(n.lower() for n in names))


def test_suggestions_survive_overpass_failure(client, fake_redis, monkeypatch):
    _mock_place_sources(monkeypatch, pois=[])
    resp = client.get("/locations/suggestions?lat=33.1667&lon=-111.5646", headers=HEADERS)
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()["candidates"]]
    # No POIs, but zones + address candidates still usable
    assert "Home" in names
    assert any("Main St" in n for n in names)


def test_suggestions_require_secret_and_coordinates(client, fake_redis, monkeypatch):
    _mock_place_sources(monkeypatch)
    # No internal secret at all -> refused at the edge, before the route runs.
    from fastapi.testclient import TestClient as _TC

    unauthenticated = _TC(geo.app)
    assert unauthenticated.get("/locations/suggestions?lat=33.0&lon=-111.0").status_code == 403
    assert client.get("/locations/suggestions?lon=-111.0", headers=HEADERS).status_code == 422
    assert client.get("/locations/suggestions?lat=95.0&lon=-111.0", headers=HEADERS).status_code == 422


def test_update_trip_place_names_and_address(client, fake_redis):
    _insert_trip(fake_redis, "t_names")
    resp = client.patch(
        "/trips/t_names",
        json={
            "start_name": "Whole Foods Market",
            "end_name": "Pizzeria Bianco",
            "start_address": "1 Main St, Phoenix, AZ",
            "end_address": "2 Oak Ave, Tempe, AZ",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["start_location"]["name"] == "Whole Foods Market"
    assert updated["start_location"]["address"] == "1 Main St, Phoenix, AZ"
    assert updated["end_location"]["name"] == "Pizzeria Bianco"
    assert updated["end_location"]["address"] == "2 Oak Ave, Tempe, AZ"
    # Coordinates + distance are locked telemetry
    assert updated["start_location"]["latitude"] == 33.1667
    assert updated["end_location"]["longitude"] == -111.63
    assert updated["distance_miles"] == 12.0

    # A picked (non-street) name wins on read: no re-resolution over the top
    locs = client.get("/trips/t_names/locations", headers=HEADERS)
    assert locs.status_code == 200
    start = locs.json()["start"]
    assert start["name"] == "Whole Foods Market"
    assert start["source"] == "stored"


def test_update_trip_rejects_bad_names_and_addresses(client, fake_redis):
    _insert_trip(fake_redis, "t_bad")
    assert (
        client.patch("/trips/t_bad", json={"start_name": "   "}, headers=HEADERS).status_code == 422
    )
    assert (
        client.patch("/trips/t_bad", json={"end_name": "x" * 121}, headers=HEADERS).status_code == 422
    )
    assert (
        client.patch("/trips/t_bad", json={"start_address": "y" * 241}, headers=HEADERS).status_code == 422
    )


def test_update_trip_names_forbidden_for_other_user(client, fake_redis):
    _insert_trip(fake_redis, "t_owned")
    resp = client.patch(
        "/trips/t_owned",
        json={"start_name": "Renamed"},
        headers={**HEADERS, "X-User-Id": "michele"},
    )
    assert resp.status_code == 403
