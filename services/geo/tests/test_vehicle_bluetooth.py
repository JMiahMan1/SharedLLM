"""Vehicles linked to Bluetooth hardware, and trips that pick their vehicle by it."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import services.geo.main as geo
from services.config import INTERNAL_SECRET
from services.geo import vehicle_bluetooth as vb
from services.geo.tests.test_trips import FakeRedis

# Made-up addresses: a head unit's classic side, its LE side (random static),
# and a phone-style rotating LE address that must never be linkable.
HEAD_UNIT = "00:1A:7D:DA:71:13"
HEAD_UNIT_LE = "C4:7D:4F:12:34:56"
ROTATING = "4A:11:22:33:44:55"
OTHER_CAR = "00:1B:DC:0F:AA:01"


class Store(FakeRedis):
    async def hdel(self, hname, key):
        self.hashes.get(hname, {}).pop(key, None)


@pytest.fixture
def store(monkeypatch):
    r = Store()

    async def _get():
        return r

    monkeypatch.setattr(geo, "get_redis", _get)

    async def _place(lat, lon):
        return {"name": "Somewhere"}

    monkeypatch.setattr(geo, "_resolve_place_details_cached", _place)
    return r


@pytest.fixture
def client(store):
    return TestClient(geo.app, headers={"X-Internal-Secret": INTERNAL_SECRET})


def _vehicle(vid, name, mpg=20.0, links=None):
    v = {"id": vid, "name": name, "mpg": mpg, "cost_per_gallon": 3.0, "fuel_type": "regular",
         "fuel_price_location": ""}
    if links is not None:
        v["bluetooth"] = links
    return v


def _save(store, v):
    store.hashes.setdefault("geo:vehicles", {})[v["id"]] = json.dumps(v)


# -- addresses ---------------------------------------------------------------


@pytest.mark.parametrize("raw", ["00:1a:7d:da:71:13", "00-1A-7D-DA-71-13", "001A7DDA7113", " 00:1A:7D:DA:71:13 "])
def test_an_address_is_normalised_whatever_way_it_is_written(raw):
    assert vb.normalise_address(raw) == HEAD_UNIT


@pytest.mark.parametrize("raw", ["", "Uconnect 1234", "00:1A:7D:DA:71", "02:00:00:00:00:00", "GG:00:00:00:00:00"])
def test_a_name_or_a_hidden_address_is_never_a_key(raw):
    with pytest.raises(vb.LinkError):
        vb.normalise_address(raw)


def test_random_le_addresses_are_told_apart_by_their_top_bits():
    assert vb.le_random_kind(HEAD_UNIT_LE) == "static"       # 0xC4 = 11......
    assert vb.le_random_kind(ROTATING) == "resolvable"       # 0x4A = 01......
    assert vb.le_random_kind("2A:11:22:33:44:55") == "non_resolvable"


def test_only_stable_addresses_are_linkable():
    assert vb.check_linkable(HEAD_UNIT, "classic") == (HEAD_UNIT, "classic")
    assert vb.check_linkable(HEAD_UNIT_LE, "le_static") == (HEAD_UNIT_LE, "le_static")
    with pytest.raises(vb.LinkError, match="changing address"):
        vb.check_linkable(ROTATING, "le_static")
    with pytest.raises(vb.LinkError):
        vb.check_linkable(ROTATING, "le_random")


def test_one_vehicle_may_have_several_addresses_but_an_address_one_vehicle():
    van = _vehicle("van", "Van")
    truck = _vehicle("truck", "Truck")
    van = vb.add_link(van, [van, truck], HEAD_UNIT, "classic", "Car Audio", "pat", now=1.0)
    van = vb.add_link(van, [van, truck], HEAD_UNIT_LE, "le_static", "Car Audio LE", "pat", now=2.0)
    assert [link["address"] for link in van["bluetooth"]] == [HEAD_UNIT, HEAD_UNIT_LE]
    with pytest.raises(vb.LinkError, match="already linked to Van"):
        vb.add_link(truck, [van, truck], HEAD_UNIT, "classic", "", "alex")


def test_linking_again_refreshes_the_name_and_keeps_one_entry():
    van = vb.add_link(_vehicle("van", "Van"), [], HEAD_UNIT, "classic", "Old name", "pat", now=1.0)
    van = vb.add_link(van, [van], HEAD_UNIT, "classic", "  New\nname ", "pat", now=2.0)
    assert len(van["bluetooth"]) == 1
    assert van["bluetooth"][0]["name"] == "New name"


def test_votes_pick_the_vehicle_seen_most_and_ties_are_stable():
    trip: dict = {}
    for vid in ["truck", "van", "van"]:
        vb.vote(trip, vid)
    vb.vote(trip, None)
    assert trip["bt_votes"] == {"truck": 1, "van": 2}
    assert vb.winner(trip["bt_votes"]) == "van"
    assert vb.winner({"van": 1, "truck": 1}) == "truck"
    assert vb.winner({}) is None


def test_settle_never_overrides_a_manual_choice():
    van, truck = _vehicle("van", "Van", 18), _vehicle("truck", "Truck", 14)
    trip = {"vehicle_id": "truck", "vehicle_name": "Truck", "vehicle_source": vb.SOURCE_MANUAL,
            "bt_votes": {"van": 5}}
    vb.settle(trip, {"van": van, "truck": truck})
    assert trip["vehicle_id"] == "truck"
    assert trip["bt_vehicle_id"] == "van"
    trip["vehicle_source"] = vb.SOURCE_DEFAULT
    vb.settle(trip, {"van": van, "truck": truck})
    assert (trip["vehicle_id"], trip["vehicle_source"], trip["mpg"]) == ("van", vb.SOURCE_BLUETOOTH, 18.0)


# -- the routes --------------------------------------------------------------


def test_link_and_unlink_through_the_api(client, store):
    _save(store, _vehicle("van", "Van"))
    r = client.post("/vehicles/van/bluetooth",
                    json={"address": "00-1a-7d-da-71-13", "kind": "classic", "name": "Car Audio", "user": "person.pat"})
    assert r.status_code == 200, r.text
    link = r.json()["vehicle"]["bluetooth"][0]
    assert link["address"] == HEAD_UNIT and link["name"] == "Car Audio" and link["linked_by"] == "pat"

    r = client.delete(f"/vehicles/van/bluetooth/{HEAD_UNIT}")
    assert r.status_code == 200
    assert r.json()["vehicle"]["bluetooth"] == []
    assert client.delete(f"/vehicles/van/bluetooth/{HEAD_UNIT}").status_code == 404


def test_an_address_another_vehicle_has_is_a_conflict(client, store):
    _save(store, _vehicle("van", "Van", links=[{"address": HEAD_UNIT, "kind": "classic", "name": ""}]))
    _save(store, _vehicle("truck", "Truck"))
    r = client.post("/vehicles/truck/bluetooth", json={"address": HEAD_UNIT, "kind": "classic"})
    assert r.status_code == 409
    assert "Van" in r.json()["detail"]


def test_a_rotating_address_is_refused_with_a_reason(client, store):
    _save(store, _vehicle("van", "Van"))
    r = client.post("/vehicles/van/bluetooth", json={"address": ROTATING, "kind": "le_static"})
    assert r.status_code == 422
    assert "pair it" in r.json()["detail"]
    assert client.post("/vehicles/nope/bluetooth", json={"address": HEAD_UNIT}).status_code == 404


def test_editing_a_vehicle_keeps_its_links(client, store):
    _save(store, _vehicle("van", "Van", links=[{"address": HEAD_UNIT, "kind": "classic", "name": "Car Audio"}]))
    r = client.post("/vehicles", json={"id": "van", "name": "Family Van", "mpg": 19, "cost_per_gallon": 3.1})
    assert r.status_code == 200
    stored = json.loads(store.hashes["geo:vehicles"]["van"])
    assert stored["name"] == "Family Van"
    assert stored["bluetooth"][0]["address"] == HEAD_UNIT


# -- trips -------------------------------------------------------------------


async def _drive(points, bt):
    now = time.time() - 600
    for i, (lat, b) in enumerate(zip(points, bt, strict=True)):
        await geo.record_point(entity_id="pat", lat=lat, lon=-104.9903, accuracy=5, speed=15.0,
                               timestamp=now + i * 30, bt=b)


@pytest.mark.asyncio
async def test_a_trip_takes_the_vehicle_whose_device_was_connected(store):
    _save(store, _vehicle("truck", "Truck", 14))
    _save(store, _vehicle("van", "Van", 18, links=[{"address": HEAD_UNIT, "kind": "classic", "name": "Car Audio"}]))
    store.data["geo:user_vehicle:pat"] = "truck"
    await _drive([39.7392, 39.7410, 39.7430], [["00:1a:7d:da:71:13"], [HEAD_UNIT], None])
    trip = json.loads(await store.get("geo:active_trip:pat"))
    assert trip["vehicle_id"] == "van"
    assert trip["vehicle_source"] == vb.SOURCE_BLUETOOTH
    assert trip["mpg"] == 18.0
    crumbs = [json.loads(p) for p, _ in store.zsets["geo:history:pat"]]
    assert [c.get("veh") for c in crumbs] == ["van", "van", None]


@pytest.mark.asyncio
async def test_without_a_connection_the_designated_vehicle_stays(store):
    _save(store, _vehicle("truck", "Truck", 14))
    _save(store, _vehicle("van", "Van", 18, links=[{"address": HEAD_UNIT, "kind": "classic", "name": ""}]))
    store.data["geo:user_vehicle:pat"] = "truck"
    await _drive([39.7392, 39.7410], [None, [OTHER_CAR]])
    trip = json.loads(await store.get("geo:active_trip:pat"))
    assert (trip["vehicle_id"], trip["vehicle_source"]) == ("truck", vb.SOURCE_DEFAULT)
    assert "bt_votes" not in trip


@pytest.mark.asyncio
async def test_the_detected_vehicle_reaches_the_finished_trip(store, monkeypatch):
    async def _match(crumbs, url):
        return {"matched": True, "confidence": 0.9, "distance_m": 2000.0}

    monkeypatch.setattr(geo.map_match, "match", _match)
    _save(store, _vehicle("van", "Van", 18, links=[{"address": HEAD_UNIT, "kind": "classic", "name": ""}]))
    await _drive([39.7392, 39.7410, 39.7430], [[HEAD_UNIT]] * 3)
    trip = json.loads(await store.get("geo:active_trip:pat"))
    done = await geo._finalize_active_trip(store, "pat", trip, time.time())
    assert done["vehicle_id"] == "van"
    assert done["vehicle_source"] == vb.SOURCE_BLUETOOTH
    assert done["bt_vehicle_id"] == "van"


def _completed(store, **extra):
    trip = {"id": "t1", "user_id": "pat", "start_time": 1.0, "distance_miles": 10.0, "mpg": 18.0,
            "cost_per_gallon": 3.0, "vehicle_id": "van", "vehicle_name": "Van",
            "vehicle_source": vb.SOURCE_BLUETOOTH, "bt_vehicle_id": "van", "activity_type": "driving", **extra}
    store.data["geo:trip:t1"] = json.dumps(trip)


def test_choosing_a_vehicle_by_hand_is_an_override_and_can_be_undone(client, store):
    _save(store, _vehicle("van", "Van", 18))
    _save(store, _vehicle("truck", "Truck", 14))
    _completed(store)

    # The edit sheet sends the vehicle on every save: the same one is not an override.
    r = client.patch("/trips/t1", json={"vehicle_id": "van", "vehicle_name": "Van", "notes": "x"},
                     headers={"X-User-Id": "pat"})
    assert r.json()["vehicle_source"] == vb.SOURCE_BLUETOOTH

    r = client.patch("/trips/t1", json={"vehicle_id": "truck", "vehicle_name": "Truck", "mpg": 14},
                     headers={"X-User-Id": "pat"})
    assert (r.json()["vehicle_id"], r.json()["vehicle_source"]) == ("truck", vb.SOURCE_MANUAL)

    r = client.patch("/trips/t1", json={"vehicle_auto": True}, headers={"X-User-Id": "pat"})
    body = r.json()
    assert (body["vehicle_id"], body["vehicle_source"], body["mpg"]) == ("van", vb.SOURCE_BLUETOOTH, 18.0)


def test_undo_without_a_detected_vehicle_is_refused(client, store):
    _completed(store, bt_vehicle_id=None)
    r = client.patch("/trips/t1", json={"vehicle_auto": True}, headers={"X-User-Id": "pat"})
    assert r.status_code == 409
