"""The daily AAA refresh that keeps Wander's fuel cost current."""
import json
from datetime import datetime

import pytest

import services.geo.main as geo


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.hashes = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def hgetall(self, name):
        return dict(self.hashes.get(name, {}))

    async def hset(self, name, key, value):
        self.hashes.setdefault(name, {})[key] = value


def _vehicle(vehicle_id, fuel_type, cost, location=""):
    return {
        "id": vehicle_id,
        "name": vehicle_id.replace("_", " ").title(),
        "mpg": 24.0,
        "cost_per_gallon": cost,
        "fuel_type": fuel_type,
        "fuel_price_location": location,
    }


@pytest.mark.asyncio
async def test_daily_refresh_updates_regular_and_diesel(monkeypatch):
    redis = FakeRedis()
    redis.hashes["geo:vehicles"] = {
        "car": json.dumps(_vehicle("car", "gasoline", 3.00, "85001")),
        "truck": json.dumps(_vehicle("truck", "diesel", 4.00, "85001")),
    }
    calls = []

    async def prices(location, *, use_cache=True):
        calls.append((location, use_cache))
        return {"source": "Phoenix metro daily average", "prices": {"regular": 3.49, "diesel": 4.29}}

    monkeypatch.setattr(geo, "_fuel_prices_for_location", prices)
    result = await geo._refresh_fuel_prices(redis, now=datetime(2026, 10, 20, 5, 0))

    assert result["updated"] == 2
    assert calls == [("85001", False)]
    car = json.loads(redis.hashes["geo:vehicles"]["car"])
    truck = json.loads(redis.hashes["geo:vehicles"]["truck"])
    assert car["cost_per_gallon"] == 3.49
    assert truck["cost_per_gallon"] == 4.29
    assert car["fuel_price_source"] == "Phoenix metro daily average"
    assert car["fuel_price_updated_at"].startswith("2026-10-20T05:00")
    assert redis.data[geo._FUEL_PRICE_DAILY_KEY] == "2026-10-20"


@pytest.mark.asyncio
async def test_daily_refresh_uses_the_home_zone_without_a_saved_zip(monkeypatch):
    redis = FakeRedis()
    redis.hashes["geo:vehicles"] = {
        "car": json.dumps(_vehicle("car", "gasoline", 3.00)),
    }
    seen = []

    async def home():
        return "33.1667,-111.5646"

    async def prices(location, *, use_cache=True):
        seen.append(location)
        return {"source": "Arizona state average", "prices": {"regular": 3.25}}

    monkeypatch.setattr(geo, "_home_fuel_location", home)
    monkeypatch.setattr(geo, "_fuel_prices_for_location", prices)
    result = await geo._refresh_fuel_prices(redis, now=datetime(2026, 10, 20, 5, 0))

    assert result["updated"] == 1
    assert seen == ["33.1667,-111.5646"]
    assert json.loads(redis.hashes["geo:vehicles"]["car"])["cost_per_gallon"] == 3.25


@pytest.mark.asyncio
async def test_daily_refresh_keeps_the_price_when_upstream_fails(monkeypatch):
    redis = FakeRedis()
    redis.hashes["geo:vehicles"] = {
        "car": json.dumps(_vehicle("car", "gasoline", 3.00, "85001")),
    }

    async def prices(location, *, use_cache=True):
        raise geo.HTTPException(status_code=502, detail="upstream down")

    monkeypatch.setattr(geo, "_fuel_prices_for_location", prices)
    result = await geo._refresh_fuel_prices(redis, now=datetime(2026, 10, 20, 5, 0))

    assert result["updated"] == 0
    assert result["errors"]
    assert json.loads(redis.hashes["geo:vehicles"]["car"])["cost_per_gallon"] == 3.00
    assert redis.data.get(geo._FUEL_PRICE_DAILY_KEY) is None


@pytest.mark.asyncio
async def test_daily_refresh_runs_only_once_per_day(monkeypatch):
    redis = FakeRedis()
    redis.hashes["geo:vehicles"] = {
        "car": json.dumps(_vehicle("car", "gasoline", 3.00, "85001")),
    }
    calls = []

    async def prices(location, *, use_cache=True):
        calls.append(location)
        return {"source": "Arizona state average", "prices": {"regular": 3.25}}

    monkeypatch.setattr(geo, "_fuel_prices_for_location", prices)
    first = await geo._refresh_fuel_prices(redis, now=datetime(2026, 10, 20, 5, 0))
    second = await geo._refresh_fuel_prices(redis, now=datetime(2026, 10, 20, 18, 0))

    assert first["updated"] == 1
    assert second.get("already_ran") is True
    assert calls == ["85001"]


@pytest.mark.asyncio
async def test_electric_vehicles_are_left_alone(monkeypatch):
    redis = FakeRedis()
    redis.hashes["geo:vehicles"] = {
        "ev": json.dumps(_vehicle("ev", "electric", 0.14, "85001")),
    }

    async def prices(location, *, use_cache=True):
        raise AssertionError("electric vehicles have no AAA gallon price")

    monkeypatch.setattr(geo, "_fuel_prices_for_location", prices)
    result = await geo._refresh_fuel_prices(redis, now=datetime(2026, 10, 20, 5, 0))

    assert result["updated"] == 0
    assert result["skipped"] == 1
