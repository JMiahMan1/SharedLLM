"""Saved Wander places (family and private) and the "save this spot?" prompt.

Every place is a HomeHub zone (services/geo/hub_places.py): geo keeps none of
its own. A private place counts only for its owner's own movements and is never
shown to anyone else. Long stays somewhere that is not a place are remembered,
and once the same spot has been worth a long stay a few times Wander offers to
save it.
"""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo
from services.geo import hub_places, visits

HOME = {"entity_id": "zone.home", "name": "Home", "latitude": 39.7392, "longitude": -104.9903, "radius": 100.0}
SPOT = (40.0225, -105.3257)


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


# --- a place, as HomeHub answers it --------------------------------------

HUB_PRIVATE = {"id": "d" * 32, "entity_id": "place." + "d" * 32, "name": "Dentist", "latitude": 1.0,
               "longitude": 2.0, "radius": 150, "passive": False, "owner": "hub-user-dad", "owner_name": "dad", "visibility": "private",
               "important": True, "notify": ["sam"], "pin": "hospital", "created_at": 1700000000.0}
HUB_FAMILY = {"id": "s" * 32, "entity_id": "zone.school", "name": "School", "latitude": 3.0, "longitude": 4.0,
              "radius": 200, "passive": False, "owner": "hub-user-dad", "owner_name": "dad", "visibility": "family", "important": False,
              "notify": [], "pin": "", "created_at": None}


def test_a_hub_zone_becomes_a_presence_zone_with_its_own_alert():
    zone = hub_places.to_zone(HUB_PRIVATE)
    assert zone["entity_id"] == "place." + "d" * 32 and zone["private"] is True and zone["custom"] is True
    assert (zone["important"], zone["notify"], zone["pin"]) == (True, ["sam"], "hospital")
    assert hub_places.to_zone({**HUB_FAMILY, "latitude": None}) is None
    saved = hub_places.as_saved_place(zone)
    assert (saved["lat"], saved["lon"], saved["scope"], saved["owner"], saved["icon"]) == (1.0, 2.0, "user", "dad",
                                                                                          "hospital")


def test_a_private_place_is_only_its_owners():
    zones = [hub_places.to_zone(HUB_PRIVATE), hub_places.to_zone(HUB_FAMILY)]
    assert [z["name"] for z in hub_places.for_person(zones, "dad")] == ["Dentist", "School"]
    assert [z["name"] for z in hub_places.for_person(zones, "sam")] == ["School"]
    assert [z["name"] for z in hub_places.family(zones)] == ["School"]
    assert hub_places.public_zone_id("place." + "d" * 32) is None
    assert hub_places.public_zone_id("zone.school") == "zone.school"


def test_with_no_homehub_there_are_no_places(monkeypatch):
    monkeypatch.setattr(hub_places, "stack_login", lambda: None)
    with pytest.raises(hub_places.PlacesUnavailable) as err:
        asyncio.run(hub_places.fetch_all())
    assert err.value.status == 503 and "HomeHub" in str(err.value)


# --- long stays somewhere unsaved ------------------------------------------


@pytest.mark.asyncio
async def test_a_short_stay_is_not_remembered():
    r = R()
    assert await visits.note_stay(r, "leo", 39.9725, -105.3257, visits.MIN_STAY_S - 1, 1000) is None
    assert await visits.list_for(r, "leo") == []


@pytest.mark.asyncio
async def test_the_same_stay_seen_twice_counts_once():
    r = R()
    since = 1000.0
    first = await visits.note_stay(r, "leo", 39.9725, -105.3257, visits.MIN_STAY_S, since)
    again = await visits.note_stay(r, "leo", 39.9726, -105.3258, visits.MIN_STAY_S + 60, since)
    assert first["count"] == 1
    assert again["count"] == 1
    assert len(await visits.list_for(r, "leo")) == 1


@pytest.mark.asyncio
async def test_a_second_visit_suggests_saving_the_spot():
    r = R()
    await visits.note_stay(r, "leo", 39.9725, -105.3257, visits.MIN_STAY_S, 1000)
    assert await visits.suggestions(r, "leo") == []
    await visits.note_stay(r, "leo", 39.9727, -105.3259, visits.MIN_STAY_S * 2, 9000)
    found = await visits.suggestions(r, "leo")
    assert len(found) == 1
    assert found[0]["count"] == 2
    assert found[0]["total_s"] >= visits.MIN_STAY_S * 3


@pytest.mark.asyncio
async def test_a_dismissed_spot_stops_being_suggested():
    r = R()
    await visits.note_stay(r, "leo", 39.9725, -105.3257, visits.MIN_STAY_S, 1000)
    await visits.note_stay(r, "leo", 39.9725, -105.3257, visits.MIN_STAY_S, 9000)
    assert await visits.dismiss(r, "leo", 39.9725, -105.3257) is True
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


def test_geo_no_longer_stores_places(routes):
    client, _ = routes
    headers = {"X-Internal-Secret": "s3cret"}
    for method, path in (("get", "/places"), ("post", "/places"), ("put", "/places/" + "a" * 32),
                         ("delete", "/places/" + "a" * 32), ("put", "/place-settings/zone.home")):
        assert getattr(client, method)(path, headers=headers).status_code in (404, 405), (method, path)


def test_routes_list_and_dismiss_a_visit_suggestion(routes):
    client, r = routes
    headers = {"X-Internal-Secret": "s3cret"}
    assert client.get("/visit-suggestions", params={"user": "leo"}, headers=headers).json()["suggestions"] == []
    asyncio.run(visits.note_stay(r, "leo", 39.9725, -105.3257, visits.MIN_STAY_S, 1000))
    asyncio.run(visits.note_stay(r, "leo", 39.9725, -105.3257, visits.MIN_STAY_S, 9000))
    found = client.get("/visit-suggestions", params={"user": "leo"}, headers=headers).json()["suggestions"]
    assert len(found) == 1
    assert found[0]["place"] == "Corner Cafe"
    bad = client.post("/visit-suggestions/dismiss", params={"user": "leo"}, headers=headers, json={"lat": "x"})
    assert bad.status_code == 422
    dismissed = client.post("/visit-suggestions/dismiss", params={"user": "leo"}, headers=headers,
                            json={"lat": 39.9725, "lon": -105.3257})
    assert dismissed.status_code == 200
    assert client.get("/visit-suggestions", params={"user": "leo"}, headers=headers).json()["suggestions"] == []


# --- wiring into the presence path ----------------------------------------


@pytest.mark.asyncio
async def test_a_place_carries_its_own_alert():
    r = R()
    geo._zone_cache.update({"at": time.time(), "zones": [hub_places.to_zone(HUB_PRIVATE), HOME]})
    try:
        assert await geo._place_alert(r, "place." + "d" * 32) == {"important": True, "notify": ["sam"]}
        assert await geo._place_alert(r, "zone.nope") == {}
    finally:
        geo._zone_cache.update({"at": 0.0, "zones": []})


@pytest.mark.asyncio
async def test_someone_elses_private_place_is_not_where_you_are(monkeypatch):
    """Only the owner's own movements are matched against a private place."""
    r = R()
    now = time.time()
    private = hub_places.to_zone(HUB_PRIVATE)
    geo._zone_cache.update({"at": now, "zones": [private, HOME]})
    sent = []

    async def notify(_r, person, kind, zone):
        sent.append((person, kind, zone["entity_id"]))

    async def nothing(*_a, **_k):
        return None

    monkeypatch.setattr(geo, "_notify_presence", notify)
    monkeypatch.setattr(geo, "_fire_place_reminders", nothing)
    try:
        for who in ("dad", "sam"):
            await geo._presence_transition(r, who, 5.0, 5.0, now - 1000, activity="still")
            await geo._presence_transition(r, who, 1.0, 2.0, now - 500, activity="still")
        assert sent == [("dad", "arrived", "place." + "d" * 32)]
    finally:
        geo._zone_cache.update({"at": 0.0, "zones": []})


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
