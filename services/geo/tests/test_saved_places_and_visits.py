"""Saved Wander places (family and private) and the "save this spot?" prompt.

A place made in Wander is a point plus a radius, stored by geo and treated
exactly like a Home Assistant zone by the presence code, so arriving at or
leaving an important one sends the same family_safety notice. Long stays
somewhere that is not a place are remembered, and once the same spot has been
worth a long stay a few times Wander offers to save it.
"""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo
from services.geo import places, visits

HOME = {"entity_id": "zone.home", "name": "Home", "latitude": 33.1667, "longitude": -111.5646, "radius": 100.0}
SPOT = (33.45, -111.90)


class R:
    def __init__(self):
        self.kv, self.h = {}, {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def hgetall(self, k):
        return dict(self.h.get(k, {}))

    async def hget(self, k, f):
        return self.h.get(k, {}).get(f)

    async def hset(self, k, f, v):
        self.h.setdefault(k, {})[f] = v

    async def hdel(self, k, f):
        return 1 if self.h.get(k, {}).pop(f, None) is not None else 0


# --- the saved place itself ------------------------------------------------


def test_validate_takes_a_name_and_a_point_and_defaults_the_rest():
    place = places.validate({"name": "  Gymnastics  ", "lat": 33.4, "lon": -111.9})
    assert place["name"] == "Gymnastics"
    assert place["scope"] == "user"
    assert place["important"] is False
    assert place["notify"] == []
    assert place["radius"] == places.DEFAULT_RADIUS_M


def test_validate_refuses_a_missing_name_and_a_point_off_the_map():
    for body in (
        {"lat": 1, "lon": 2},
        {"name": "x", "lat": 91, "lon": 0},
        {"name": "x", "lat": 0, "lon": 181},
        {"name": "   ", "lat": 1, "lon": 2},
        "nope",
    ):
        with pytest.raises(ValueError):
            places.validate(body)


def test_validate_clamps_the_radius_and_keeps_the_chosen_scope():
    small = places.validate({"name": "x", "lat": 1, "lon": 2, "radius": 5, "scope": "family"})
    big = places.validate({"name": "x", "lat": 1, "lon": 2, "radius": 99999})
    assert small["radius"] == places.MIN_RADIUS_M
    assert small["scope"] == "family"
    assert big["radius"] == places.MAX_RADIUS_M


def test_validate_cleans_the_notify_list():
    place = places.validate({"name": "x", "lat": 1, "lon": 2, "important": True, "notify": ["Michele", "michele", " Leo "]})
    assert place["important"] is True
    assert place["notify"] == ["leo", "michele"]


@pytest.mark.asyncio
async def test_a_saved_place_round_trips_and_carries_its_owner():
    r = R()
    made = await places.add(r, "Dad", places.validate({"name": "Gymnastics", "lat": 33.4, "lon": -111.9}))
    assert made["owner"] == "dad"
    assert places._PLACE_ID.match(made["id"])
    assert (await places.get(r, made["id"]))["name"] == "Gymnastics"
    assert places.entity_id(made["id"]) == f"place.{made['id']}"
    assert places.place_id_from_entity(f"place.{made['id']}") == made["id"]
    assert places.place_id_from_entity("zone.home") is None
    assert await places.get(r, "not-an-id") is None


@pytest.mark.asyncio
async def test_a_private_place_is_only_visible_to_its_owner():
    r = R()
    await places.add(r, "dad", places.validate({"name": "Dentist", "lat": 1, "lon": 2}))
    await places.add(r, "dad", places.validate({"name": "School", "lat": 3, "lon": 4, "scope": "family"}))
    assert {p["name"] for p in await places.list_for(r, "dad")} == {"Dentist", "School"}
    assert [p["name"] for p in await places.list_for(r, "michele")] == ["School"]
    assert len(await places.list_all(r)) == 2


@pytest.mark.asyncio
async def test_only_the_owner_or_an_admin_may_change_a_place():
    r = R()
    private = await places.add(r, "dad", places.validate({"name": "Dentist", "lat": 1, "lon": 2}))
    assert places.can_see(private, "dad")
    assert not places.can_see(private, "michele")
    assert places.can_edit(private, "dad")
    assert not places.can_edit(private, "michele")
    assert places.can_edit(private, "michele", is_admin=True)
    family = await places.add(r, "dad", places.validate({"name": "School", "lat": 3, "lon": 4, "scope": "family"}))
    assert places.can_see(family, "michele")
    assert not places.can_edit(family, "michele")


@pytest.mark.asyncio
async def test_update_merges_and_remove_forgets():
    r = R()
    made = await places.add(r, "dad", places.validate({"name": "Dentist", "lat": 1, "lon": 2}))
    merged = await places.update(r, made["id"], {"name": "New Dentist", "important": True})
    assert merged["name"] == "New Dentist"
    assert merged["important"] is True
    assert merged["owner"] == "dad"
    assert merged["id"] == made["id"]
    assert await places.update(r, "nope", {"name": "x"}) is None
    assert await places.remove(r, made["id"]) is True
    assert await places.remove(r, made["id"]) is False


@pytest.mark.asyncio
async def test_a_saved_place_becomes_a_zone_and_carries_its_own_alert():
    r = R()
    made = await places.add(r, "dad", places.validate({
        "name": "Gymnastics", "lat": 33.4, "lon": -111.9, "radius": 200,
        "important": True, "notify": ["Michele"],
    }))
    zone = places.to_zone(made)
    assert zone["entity_id"] == f"place.{made['id']}"
    assert zone["name"] == "Gymnastics"
    assert zone["radius"] == 200
    assert zone["custom"] is True
    assert places.to_answer(made)["entity_id"] == f"place.{made['id']}"
    assert places.alert_for(made) == {"important": True, "notify": ["michele"]}


# --- long stays somewhere unsaved ------------------------------------------


@pytest.mark.asyncio
async def test_a_short_stay_is_not_remembered():
    r = R()
    assert await visits.note_stay(r, "leo", 33.4, -111.9, visits.MIN_STAY_S - 1, 1000) is None
    assert await visits.list_for(r, "leo") == []


@pytest.mark.asyncio
async def test_the_same_stay_seen_twice_counts_once():
    r = R()
    since = 1000.0
    first = await visits.note_stay(r, "leo", 33.4, -111.9, visits.MIN_STAY_S, since)
    again = await visits.note_stay(r, "leo", 33.4001, -111.9001, visits.MIN_STAY_S + 60, since)
    assert first["count"] == 1
    assert again["count"] == 1
    assert len(await visits.list_for(r, "leo")) == 1


@pytest.mark.asyncio
async def test_a_second_visit_suggests_saving_the_spot():
    r = R()
    await visits.note_stay(r, "leo", 33.4, -111.9, visits.MIN_STAY_S, 1000)
    assert await visits.suggestions(r, "leo") == []
    await visits.note_stay(r, "leo", 33.4002, -111.9002, visits.MIN_STAY_S * 2, 9000)
    found = await visits.suggestions(r, "leo")
    assert len(found) == 1
    assert found[0]["count"] == 2
    assert found[0]["total_s"] >= visits.MIN_STAY_S * 3


@pytest.mark.asyncio
async def test_a_dismissed_spot_stops_being_suggested():
    r = R()
    await visits.note_stay(r, "leo", 33.4, -111.9, visits.MIN_STAY_S, 1000)
    await visits.note_stay(r, "leo", 33.4, -111.9, visits.MIN_STAY_S, 9000)
    assert await visits.dismiss(r, "leo", 33.4, -111.9) is True
    assert await visits.suggestions(r, "leo") == []
    assert await visits.dismiss(r, "leo", 40.0, -100.0) is False


# --- the HTTP routes -------------------------------------------------------


@pytest.fixture
def routes(monkeypatch):
    r = R()

    async def redis():
        return r

    async def zones():
        return [HOME]

    async def place_name(lat, lon):
        return "Corner Cafe"

    monkeypatch.setattr(geo, "get_redis", redis)
    monkeypatch.setattr(geo, "_zones", zones)
    monkeypatch.setattr(geo, "_resolve_place_name_cached", place_name)
    monkeypatch.setattr(geo, "INTERNAL_SECRET", "s3cret")
    return TestClient(geo.app), r


def test_routes_add_list_change_and_remove_a_place(routes):
    client, _ = routes
    headers = {"X-Internal-Secret": "s3cret"}
    made = client.post("/places", params={"owner": "dad"}, headers=headers,
                       json={"name": "Gymnastics", "lat": 33.4, "lon": -111.9, "scope": "family"})
    assert made.status_code == 200
    place = made.json()["place"]
    assert place["owner"] == "dad"
    assert place["entity_id"].startswith("place.")
    listed = client.get("/places", params={"viewer": "michele"}, headers=headers).json()["places"]
    assert [p["id"] for p in listed] == [place["id"]]
    changed = client.put(f"/places/{place['id']}", headers=headers, json={"important": True, "notify": ["michele"]})
    assert changed.json()["place"]["important"] is True
    assert client.delete(f"/places/{place['id']}", headers=headers).status_code == 200
    assert client.delete(f"/places/{place['id']}", headers=headers).status_code == 404


def test_routes_refuse_a_bad_place_and_a_missing_secret(routes):
    client, _ = routes
    headers = {"X-Internal-Secret": "s3cret"}
    assert client.post("/places", params={"owner": "dad"}, headers=headers, json={"lat": 1, "lon": 2}).status_code == 422
    assert client.post("/places", params={"owner": "dad"}, headers=headers,
                       json={"name": "x", "lat": 91, "lon": 0}).status_code == 422
    assert client.put("/places/deadbeef", headers=headers, json={"name": "x"}).status_code == 404
    assert client.get("/places", params={"viewer": "dad"}).status_code == 403


def test_routes_list_and_dismiss_a_visit_suggestion(routes):
    client, r = routes
    headers = {"X-Internal-Secret": "s3cret"}
    assert client.get("/visit-suggestions", params={"user": "leo"}, headers=headers).json()["suggestions"] == []
    asyncio.run(visits.note_stay(r, "leo", 33.4, -111.9, visits.MIN_STAY_S, 1000))
    asyncio.run(visits.note_stay(r, "leo", 33.4, -111.9, visits.MIN_STAY_S, 9000))
    found = client.get("/visit-suggestions", params={"user": "leo"}, headers=headers).json()["suggestions"]
    assert len(found) == 1
    assert found[0]["place"] == "Corner Cafe"
    bad = client.post("/visit-suggestions/dismiss", params={"user": "leo"}, headers=headers, json={"lat": "x"})
    assert bad.status_code == 422
    dismissed = client.post("/visit-suggestions/dismiss", params={"user": "leo"}, headers=headers,
                            json={"lat": 33.4, "lon": -111.9})
    assert dismissed.status_code == 200
    assert client.get("/visit-suggestions", params={"user": "leo"}, headers=headers).json()["suggestions"] == []


# --- wiring into the presence path ----------------------------------------


@pytest.mark.asyncio
async def test_a_saved_place_carries_its_own_alert_and_a_zone_uses_the_family_settings():
    r = R()
    made = await places.add(r, "dad", places.validate({
        "name": "Gym", "lat": 1, "lon": 2, "important": True, "notify": ["michele"],
    }))
    assert await geo._place_alert(r, f"place.{made['id']}") == {"important": True, "notify": ["michele"]}
    assert await geo._place_alert(r, "zone.nope") == {}


@pytest.mark.asyncio
async def test_the_sweep_remembers_a_long_stay_somewhere_unsaved(monkeypatch):
    r = R()
    now = time.time()
    geo._zone_cache.update({"at": now, "zones": [HOME]})
    try:
        async def out(_r):
            return {"leo": {"lat": SPOT[0], "lon": SPOT[1], "since": now - 45 * 60, "t": now - 60, "notified": False}}

        monkeypatch.setattr(geo.safety, "stops", out)
        assert await geo._sweep_visit_stays(r, now) == 1
        recorded = await visits.list_for(r, "leo")
        assert len(recorded) == 1
        assert recorded[0]["count"] == 1

        # A long stay inside a saved place is not a suggestion.
        async def home(_r):
            return {"leo": {"lat": HOME["latitude"], "lon": HOME["longitude"], "since": now - 45 * 60, "t": now - 60, "notified": False}}

        monkeypatch.setattr(geo.safety, "stops", home)
        assert await geo._sweep_visit_stays(r, now) == 0
    finally:
        geo._zone_cache.update({"at": 0.0, "zones": []})
