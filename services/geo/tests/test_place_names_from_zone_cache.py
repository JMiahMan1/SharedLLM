"""Place names come from the cached family places (all HomeHub zones), not a hub read per point.

Trip lists resolve many points. ``_closest_ha_zone`` used to read every hub state
for each one (and, with Home Assistant down, tried it first every time). It now
reads the same five-minute cache as ``/place-name`` and presence; a HomeHub that
does not answer keeps the last places, and nobody's private place names a spot.
"""
import time

import pytest

import services.geo.main as geo

HUB_HOME = {"entity_id": "zone.home", "name": "Home", "latitude": 10.0, "longitude": 20.0, "radius": 100.0}
SAVED_PARK = {"entity_id": "place.p1", "name": "The Park", "latitude": 10.01, "longitude": 20.0, "radius": 150.0,
              "custom": True}


@pytest.fixture(autouse=True)
def fresh_cache():
    geo._zone_cache.update({"at": 0.0, "zones": []})
    yield
    geo._zone_cache.update({"at": 0.0, "zones": []})


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


PRIVATE_DEN = {"entity_id": "place." + "d" * 32, "name": "Secret Den", "latitude": 11.0, "longitude": 21.0,
               "radius": 150.0, "custom": True, "private": True, "owner": "sam"}


async def test_a_homehub_that_does_not_answer_keeps_the_last_places(monkeypatch):
    async def hub_places():
        return [HUB_HOME, SAVED_PARK]

    monkeypatch.setattr(geo, "_zones", hub_places)
    assert [z["name"] for z in await geo._zones_cached()] == ["Home", "The Park"]

    async def hub_down():
        raise geo.hub_places.PlacesUnavailable("HomeHub did not answer", 502)

    monkeypatch.setattr(geo, "_zones", hub_down)
    monkeypatch.setattr(geo.hub_places, "stack_login", lambda: ("http://hub.test", "s"))
    geo._zone_cache["at"] = 0.0
    assert [z["name"] for z in await geo._zones_cached()] == ["Home", "The Park"]
    # No HomeHub at all: no places, rather than a second store.
    monkeypatch.setattr(geo.hub_places, "stack_login", lambda: None)
    geo._zone_cache["at"] = 0.0
    assert await geo._zones_cached() == []


async def test_a_private_place_never_names_a_spot_for_anyone(no_state_reads):
    geo._zone_cache.update({"at": time.time(), "zones": [HUB_HOME, PRIVATE_DEN]})
    assert await geo._closest_ha_zone(11.0, 21.0) is None
    assert await geo._ha_zones_containing(11.0, 21.0) == []
