"""Place names come from the cached family places (hub zones + saved places), not a hub read per point.

Trip lists resolve many points. ``_closest_ha_zone`` used to read every hub state
for each one (and, with Home Assistant down, tried it first every time). It now
reads the same five-minute cache as ``/place-name`` and presence, and a hub that
does not answer no longer takes the saved places down with it.
"""
import time

import pytest

import services.geo.main as geo

HUB_HOME = {"entity_id": "zone.home", "name": "Home", "latitude": 10.0, "longitude": 20.0, "radius": 100.0}
SAVED_PARK = {"entity_id": "place.p1", "name": "The Park", "latitude": 10.01, "longitude": 20.0, "radius": 150.0,
              "custom": True}


@pytest.fixture(autouse=True)
def fresh_cache():
    geo._zone_cache.update({"at": 0.0, "zones": [], "hub": []})
    yield
    geo._zone_cache.update({"at": 0.0, "zones": [], "hub": []})


@pytest.fixture
def no_state_reads(monkeypatch):
    async def forbidden():
        raise AssertionError("a place-name lookup must not read every hub state")

    monkeypatch.setattr(geo, "_ha_get_states", forbidden)


async def test_the_closest_family_place_comes_from_the_cache(monkeypatch, no_state_reads):
    geo._zone_cache.update({"at": time.time(), "zones": [HUB_HOME, SAVED_PARK]})
    assert await geo._closest_ha_zone(10.0, 20.0) == "Home"
    assert await geo._closest_ha_zone(10.01, 20.0) == "The Park"
    assert await geo._closest_ha_zone(11.0, 21.0) is None


async def test_hub_zone_suggestions_come_from_the_cache_and_leave_saved_places_out(no_state_reads):
    geo._zone_cache.update({"at": time.time(), "zones": [HUB_HOME, SAVED_PARK]})
    (zone,) = await geo._ha_zones_containing(10.0, 20.0)
    assert zone["name"] == "Home" and zone["kind"] == "zone" and zone["distance_m"] == 0
    assert await geo._ha_zones_containing(10.01, 20.0) == []


async def test_a_hub_that_does_not_answer_keeps_the_saved_places_and_its_last_zones(monkeypatch):
    async def hub_zones():
        return [HUB_HOME]

    async def saved():
        return [SAVED_PARK]

    monkeypatch.setattr(geo, "_zones", hub_zones)
    monkeypatch.setattr(geo, "_saved_places", saved)
    assert [z["name"] for z in await geo._zones_cached()] == ["Home", "The Park"]

    async def hub_down():
        raise ConnectionError("hub down")

    monkeypatch.setattr(geo, "_zones", hub_down)
    geo._zone_cache["at"] = 0.0
    assert [z["name"] for z in await geo._zones_cached()] == ["Home", "The Park"]

    geo._zone_cache.update({"at": 0.0, "hub": []})
    assert [z["name"] for z in await geo._zones_cached()] == ["The Park"]
    assert await geo._closest_ha_zone(10.01, 20.0) == "The Park"


def test_geo_sends_the_stack_login_only_to_homehub(monkeypatch):
    import services.config as config
    import services.geo.main as geo

    monkeypatch.setattr(geo, "HA_URL", "http://real-ha.test:8123")
    monkeypatch.setattr(geo, "HA_TOKEN", None)
    monkeypatch.setattr(config, "HOMEHUB_URL", "http://hub.test:8123")
    monkeypatch.setattr(config, "INTERNAL_SECRET", "made-up-secret")
    assert geo._hub_target() == ("http://hub.test:8123", "made-up-secret")
    monkeypatch.setattr(geo, "HA_TOKEN", "household-token")
    assert geo._hub_target() == ("http://real-ha.test:8123", "household-token")
