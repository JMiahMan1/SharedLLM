"""Forward geocoding of event addresses for drive times (Nominatim, cached)."""
import json

import pytest

import services.geo.main as geo


class R:
    def __init__(self):
        self.kv = {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None):
        self.kv[k] = v


class Resp:
    def __init__(self, data):
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self, content_type=None):
        return self.data


class Client:
    def __init__(self, data):
        self.data, self.calls = data, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        return Resp(self.data)


@pytest.fixture
def env(monkeypatch):
    r = R()

    async def redis():
        return r

    monkeypatch.setattr(geo, "get_redis", redis)
    return r


@pytest.mark.asyncio
async def test_geocodes_near_the_person_and_caches(env, monkeypatch):
    client = Client([{"lat": "33.42", "lon": "-111.83"}])
    monkeypatch.setattr(geo, "get_client_insecure", lambda: client)
    hit = await geo._geocode_place("  12 Oak St,   Mesa ", near=(33.4, -111.8))
    assert hit == {"name": "12 Oak St, Mesa", "latitude": 33.42, "longitude": -111.83, "radius": 100.0}
    assert "viewbox" in client.calls[0]
    again = await geo._geocode_place("12 oak st, mesa")
    assert again == hit and len(client.calls) == 1  # from the cache


@pytest.mark.asyncio
async def test_a_miss_is_remembered(env, monkeypatch):
    client = Client([])
    monkeypatch.setattr(geo, "get_client_insecure", lambda: client)
    assert await geo._geocode_place("Nowhere at all") is None
    assert json.loads(env.kv["geo:geocode:nowhere at all"]) is None
    assert await geo._geocode_place("Nowhere at all") is None
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_zone_names_win_over_geocoding(env, monkeypatch):
    zone = {"entity_id": "zone.work", "name": "Work", "latitude": 1.0, "longitude": 2.0, "radius": 150.0}

    async def zones():
        return [zone]

    monkeypatch.setattr(geo, "_zones", zones)
    monkeypatch.setattr(geo, "get_client_insecure", lambda: (_ for _ in ()).throw(AssertionError("no lookup")))
    assert await geo._destination("work") == zone
    assert (await geo._destination("33.1, -111.2"))["latitude"] == 33.1
