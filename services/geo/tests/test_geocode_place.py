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


class Routed:
    """A client answering per base URL; an Exception value means unreachable."""

    def __init__(self, answers):
        self.answers, self.urls = answers, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.urls.append(url)
        for base, data in self.answers.items():
            if url.startswith(base):
                if isinstance(data, Exception):
                    raise data
                return Resp(data)
        raise AssertionError(url)


@pytest.mark.asyncio
async def test_the_self_hosted_geocoder_answers_first(monkeypatch):
    client = Routed({"http://nominatim:8080": {"display_name": "Home St"}, geo.NOMINATIM_PUBLIC_URL: {}})
    monkeypatch.setattr(geo, "NOMINATIM_URL", "http://nominatim:8080")
    monkeypatch.setattr(geo, "get_client_insecure", lambda: client)
    assert await geo._nominatim("reverse", {"lat": 1, "lon": 2}) == {"display_name": "Home St"}
    assert client.urls == ["http://nominatim:8080/reverse"]


@pytest.mark.asyncio
async def test_outside_its_regions_or_down_the_public_one_answers(monkeypatch):
    monkeypatch.setattr(geo, "NOMINATIM_URL", "http://nominatim:8080")
    far = Routed({"http://nominatim:8080": {"error": "Unable to geocode"},
                  geo.NOMINATIM_PUBLIC_URL: {"display_name": "Far away"}})
    monkeypatch.setattr(geo, "get_client_insecure", lambda: far)
    assert (await geo._nominatim("reverse", {"lat": 1, "lon": 2}))["display_name"] == "Far away"
    down = Routed({"http://nominatim:8080": OSError("importing"), geo.NOMINATIM_PUBLIC_URL: [{"lat": "1"}]})
    monkeypatch.setattr(geo, "get_client_insecure", lambda: down)
    assert await geo._nominatim("search", {"q": "x"}) == [{"lat": "1"}]


@pytest.mark.asyncio
async def test_no_place_anywhere_is_an_empty_answer_not_an_error(monkeypatch):
    monkeypatch.setattr(geo, "NOMINATIM_URL", "http://nominatim:8080")
    client = Routed({"http://nominatim:8080": [], geo.NOMINATIM_PUBLIC_URL: OSError("offline")})
    monkeypatch.setattr(geo, "get_client_insecure", lambda: client)
    assert await geo._nominatim("search", {"q": "nowhere"}) == []
