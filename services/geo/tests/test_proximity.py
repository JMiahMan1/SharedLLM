"""Family phones near each other over Bluetooth LE, and trips taken together."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo
from services.config import INTERNAL_SECRET
from services.geo import proximity as px
from services.geo.tests.test_trips import FakeRedis

NOW = 1_800_000_000.0


def _trip(tid, user, start, minutes, **extra):
    return {"id": tid, "user_id": user, "user_name": user.title(), "start_time": start,
            "end_time": start + minutes * 60, "vehicle_id": "truck", "vehicle_name": "Truck",
            "vehicle_source": "default", **extra}


def _seen(observer, person, band, first, last):
    return {"observer": observer, "person": person, "band": band, "first_seen": first, "last_seen": last, "samples": 5}


# -- cleaning ----------------------------------------------------------------


def test_a_sighting_is_kept_only_with_sane_fields():
    good = px.clean_event({"person": "Person.Gracie", "band": "same_room", "first_seen": NOW - 120,
                           "last_seen": NOW - 60, "samples": 7, "rssi": -70}, "pat", NOW)
    assert good == {"observer": "pat", "person": "gracie", "band": "same_room", "first_seen": NOW - 120,
                    "last_seen": NOW - 60, "samples": 7, "rssi": -70}
    base = {"person": "gracie", "band": "nearby", "first_seen": NOW - 10, "last_seen": NOW}
    assert px.clean_event({**base, "band": "3 metres"}, "pat", NOW) is None
    assert px.clean_event({**base, "person": "pat"}, "pat", NOW) is None      # yourself
    assert px.clean_event({**base, "last_seen": NOW + 3600}, "pat", NOW) is None  # future
    assert px.clean_event({**base, "first_seen": NOW + 5}, "pat", NOW) is None    # backwards
    assert px.clean_event({**base, "rssi": 400}, "pat", NOW).get("rssi") is None


# -- together ----------------------------------------------------------------


def test_close_phones_on_overlapping_trips_are_travelling_together():
    a = _trip("a", "pat", NOW, 30)
    g = _trip("g", "gracie", NOW + 60, 29)
    events = [_seen("pat", "gracie", "next_to", NOW + 60, NOW + 900),
              _seen("gracie", "pat", "same_room", NOW + 900, NOW + 1500)]
    assert px.together(a, g, events) == {"via": ["nearby"], "vehicle_id": None}


def test_far_or_brief_sightings_are_not_enough():
    a = _trip("a", "pat", NOW, 30)
    g = _trip("g", "gracie", NOW, 30)
    assert px.together(a, g, [_seen("pat", "gracie", "nearby", NOW, NOW + 1800)]) is None
    assert px.together(a, g, [_seen("pat", "gracie", "next_to", NOW, NOW + 60)]) is None
    # Someone else's sighting says nothing about these two.
    assert px.together(a, g, [_seen("pat", "alex", "next_to", NOW, NOW + 1800)]) is None


def test_trips_that_do_not_overlap_are_never_together():
    a = _trip("a", "pat", NOW, 30)
    g = _trip("g", "gracie", NOW + 3600, 30)
    assert px.together(a, g, [_seen("pat", "gracie", "next_to", NOW, NOW + 7200)]) is None


def test_the_same_linked_car_is_enough_on_its_own():
    a = _trip("a", "pat", NOW, 30, vehicle_id="van", vehicle_name="Van", vehicle_source="bluetooth", bt_vehicle_id="van")
    g = _trip("g", "gracie", NOW + 120, 25, vehicle_id="van", vehicle_name="Van", vehicle_source="bluetooth")
    assert px.together(a, g, []) == {"via": ["same_vehicle"], "vehicle_id": "van"}
    # A hand-picked vehicle is not a detection.
    g2 = _trip("g", "gracie", NOW + 120, 25, vehicle_id="van", vehicle_source="manual")
    a2 = _trip("a", "pat", NOW, 30, vehicle_id="van", vehicle_source="manual")
    assert px.together(a2, g2, []) is None


def test_annotation_names_the_person_and_the_car():
    a = _trip("a", "pat", NOW, 30, vehicle_id="van", vehicle_name="Van", vehicle_source="bluetooth")
    g = _trip("g", "gracie", NOW, 30)
    trips = px.annotate_together([a, g], [_seen("gracie", "pat", "next_to", NOW, NOW + 1800)])
    assert trips[0]["together"] == [{"user_id": "gracie", "user_name": "Gracie", "trip_id": "g", "via": ["nearby"],
                                     "vehicle_id": None, "vehicle_name": "Van"}]
    assert trips[1]["together"][0]["user_id"] == "pat"
    assert trips[1]["together"][0]["vehicle_name"] == "Van"


def test_near_now_takes_the_closest_band_from_either_side():
    events = [_seen("pat", "gracie", "nearby", NOW - 100, NOW - 50),
              _seen("gracie", "pat", "next_to", NOW - 200, NOW - 30),
              _seen("alex", "pat", "same_room", NOW - 5000, NOW - 4000)]
    assert px.near_now(events, "pat", NOW) == [{"user_id": "gracie", "band": "next_to", "last_seen": NOW - 30}]


# -- routes ------------------------------------------------------------------


class Store(FakeRedis):
    async def hset(self, hname, key, value):
        self.hashes.setdefault(hname, {})[key] = value


@pytest.fixture
def store(monkeypatch):
    r = Store()

    async def _get():
        return r

    async def _sharing():
        return {"users": [
            {"username": "pat", "enabled": True, "audience": "circle"},
            {"username": "gracie", "enabled": True, "audience": "circle"},
            {"username": "alex", "enabled": True, "audience": "circle", "paused": True},
        ]}

    monkeypatch.setattr(geo, "get_redis", _get)
    monkeypatch.setattr(geo, "_fetch_activity_sharing", _sharing)
    return r


@pytest.fixture
def client(store):
    return TestClient(geo.app, headers={"X-Internal-Secret": INTERNAL_SECRET})


def test_config_makes_one_household_key_and_lists_the_members(client):
    first = client.get("/proximity/config", params={"user": "person.pat"}).json()
    again = client.get("/proximity/config", params={"user": "pat"}).json()
    assert first["key"] == again["key"] and len(first["key"]) == 44
    assert first["key_id"] == px.key_id(first["key"])
    assert first["members"] == ["alex", "gracie", "pat"]
    assert first["period_s"] == px.PERIOD_S
    # The offline chat has its own key, kept like the proximity one.
    assert first["chat_key"] == again["chat_key"] and first["chat_key"] != first["key"]
    assert first["chat_key_id"] == px.key_id(first["chat_key"])
    rotated = client.post("/proximity/key/rotate").json()
    assert rotated["key_id"] != first["key_id"]


def test_sightings_are_stored_but_never_of_or_by_a_paused_person(client, store):
    now = time.time()
    ev = {"band": "same_room", "first_seen": now - 120, "last_seen": now - 60}
    r = client.post("/proximity/pat", json={"events": [{**ev, "person": "gracie"}, {**ev, "person": "alex"}, "junk"]})
    assert r.json()["kept"] == 1
    assert len(store.zsets["geo:proximity:pat"]) == 1
    r = client.post("/proximity/alex", json={"events": [{**ev, "person": "pat"}]})
    assert r.json()["kept"] == 0
    assert client.post("/proximity/pat", json={"events": "nope"}).status_code == 422


def test_near_now_route(client, store):
    now = time.time()
    store.zsets["geo:proximity:gracie"] = [(json.dumps(_seen("gracie", "pat", "next_to", now - 60, now - 10)), now - 10)]
    near = client.get("/proximity/near", params={"viewer": "pat"}).json()["near"]
    assert [n["user_id"] for n in near] == ["gracie"]
    assert near[0]["band"] == "next_to"


def test_trips_list_says_who_travelled_together(client, store):
    now = time.time() - 7200
    for t in (_trip("trip_pat_1", "pat", now, 30), _trip("trip_gracie_1", "gracie", now + 30, 29)):
        store.data[f"geo:trip:{t['id']}"] = json.dumps(t)
        store.zsets.setdefault(f"geo:trips:user:{t['user_id']}", []).append((t["id"], t["start_time"]))
    store.zsets["geo:proximity:pat"] = [(json.dumps(_seen("pat", "gracie", "next_to", now, now + 1700)), now + 1700)]
    r = client.get("/trips", params={"scope": "family", "viewer": "pat"})
    assert r.status_code == 200, r.text
    mine = next(t for t in r.json()["trips"] if t["user_id"] == "pat")
    assert mine["together"][0]["user_id"] == "gracie"
