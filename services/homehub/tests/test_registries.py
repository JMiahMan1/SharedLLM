"""Floors, labels, categories, zones, persons and tags: the registries a person edits from the Devices page.

Every name, id and coordinate here is made up.
"""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import registries
from services.homehub.api import build_app
from services.homehub.auth import Auth, Principal
from services.homehub.core import Area, ConfigEntry, DeviceEntry, EntityEntry, Hub
from services.homehub.drivers import mobile_app
from services.homehub.importer import import_config_dir
from services.homehub.store import Store
from services.homehub.tests.test_flows import FakeRuntime

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture
def env(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7", location={"location_name": "Home", "latitude": 10.0, "longitude": 20.0,
                                                "radius": 100})
    hub.store = store
    hub.areas["den"] = Area("den", "Den")
    store.save_area(hub.areas["den"])
    entry = ConfigEntry("e1", "test", "Test")
    hub.config_entries["e1"] = entry
    hub.devices["d1"] = DeviceEntry("d1", name="Lamp", config_entries=["e1"])
    store.save_device(hub.devices["d1"])
    for ent in (EntityEntry("light.lamp", "test", "u1", "e1", "d1"),
                EntityEntry("device_tracker.pixel", "mobile_app", "t1", "e1", "d1"),
                EntityEntry("device_tracker.tablet", "mobile_app", "t2", "e1", "d1")):
        hub.entities[ent.entity_id] = ent
        store.save_entity(ent)
    hub.states.set("light.lamp", "on", {})
    rt = FakeRuntime(hub, store)
    client = TestClient(build_app(rt, Auth(store, SECRET)))
    return hub, store, rt, client


def test_every_route_is_admin_only(env):
    _hub, _store, _rt, c = env
    for path in ("/hub/floors", "/hub/labels", "/hub/categories", "/hub/zones", "/hub/persons", "/hub/tags"):
        assert c.get(path).status_code == 401, path
    assert c.post("/hub/floors", json={"name": "Upstairs"}).status_code == 401
    assert c.post("/hub/zones/import", json={"places": []}).status_code == 401
    assert c.patch("/hub/zones/home", json={"latitude": 1.0, "longitude": 2.0}).status_code == 401


class _MemberAuth(Auth):
    """A signed-in household member who is not an admin (a child's login)."""

    def authenticate(self, token):
        if token == "member-token":
            return Principal(user_id="u-member", name="Member", is_admin=False)
        return super().authenticate(token)


def test_a_member_cannot_add_rename_or_remove_a_room(env):
    hub, store, rt, _c = env
    c = TestClient(build_app(rt, _MemberAuth(store, SECRET)))
    member = {"Authorization": "Bearer member-token"}
    # The hub answers every admin-only route with 401 for a principal that is not an admin.
    assert c.post("/hub/areas", headers=member, json={"name": "Fort"}).status_code == 401
    assert c.patch("/hub/areas/den", headers=member, json={"name": "Clubhouse"}).status_code == 401
    assert c.delete("/hub/areas/den", headers=member).status_code == 401
    assert sorted(hub.areas) == ["den"] and hub.areas["den"].name == "Den"
    # The same routes still work for an admin, so the refusal is about who asks.
    assert c.post("/hub/areas", headers=H, json={"name": "Fort"}).status_code == 200


def test_floors_are_made_renamed_and_removed_and_areas_pick_one(env):
    hub, store, _rt, c = env
    r = c.post("/hub/floors", headers=H, json={"name": "Upstairs", "level": 1})
    assert r.status_code == 200 and r.json() == {"floor_id": "upstairs", "name": "Upstairs", "level": 1,
                                                 "icon": None, "aliases": []}
    assert c.post("/hub/floors", headers=H, json={"name": "upstairs"}).status_code == 409
    assert c.post("/hub/floors", headers=H, json={"name": "Basement", "level": "low"}).status_code == 400
    assert c.post("/hub/floors", headers=H, json={"name": "Basement", "level": -1}).status_code == 200
    assert [f["name"] for f in c.get("/hub/floors", headers=H).json()["floors"]] == ["Basement", "Upstairs"]

    # An area picks a floor the hub has, and only one it has.
    assert c.patch("/hub/areas/den", headers=H, json={"floor_id": "attic"}).status_code == 400
    r = c.patch("/hub/areas/den", headers=H, json={"floor_id": "upstairs"})
    assert r.status_code == 200 and r.json()["floor_id"] == "upstairs"
    r = c.post("/hub/areas", headers=H, json={"name": "Cellar", "floor_id": "basement"})
    assert r.status_code == 200 and hub.areas["cellar"].floor_id == "basement"

    r = c.patch("/hub/floors/upstairs", headers=H, json={"name": "First floor", "icon": "mdi:home-floor-1"})
    assert r.json()["name"] == "First floor" and r.json()["floor_id"] == "upstairs"
    assert c.patch("/hub/floors/nope", headers=H, json={"name": "X"}).status_code == 404
    r = c.delete("/hub/floors/upstairs", headers=H)
    assert r.status_code == 200 and r.json()["areas"] == ["den"]
    assert hub.areas["den"].floor_id is None
    assert "upstairs" not in store.get_kv("floors") and "upstairs" not in hub.floors
    assert [a.floor_id for a in store.load_areas() if a.area_id == "den"] == [None]


def test_labels_go_on_areas_devices_and_entities_and_come_off_when_removed(env):
    hub, store, _rt, c = env
    r = c.post("/hub/labels", headers=H, json={"name": "Night lights", "color": "indigo"})
    assert r.status_code == 200 and r.json()["label_id"] == "night_lights"
    assert c.post("/hub/labels", headers=H, json={"name": "night lights"}).status_code == 409
    assert c.post("/hub/labels", headers=H, json={"name": "Bad", "color": "red; x"}).status_code == 400
    assert c.post("/hub/labels", headers=H, json={"name": "Kids", "color": "#33aa55"}).status_code == 200

    assert c.patch("/hub/devices/d1", headers=H, json={"labels": ["ghost"]}).status_code == 400
    r = c.patch("/hub/devices/d1", headers=H, json={"labels": ["night_lights", "kids", "kids"]})
    assert r.status_code == 200 and r.json()["labels"] == ["night_lights", "kids"]
    r = c.patch("/hub/entities/light.lamp", headers=H, json={"labels": ["kids"]})
    assert r.status_code == 200 and r.json()["labels"] == ["kids"]
    assert c.patch("/hub/entities/light.lamp", headers=H, json={"labels": "kids"}).status_code == 400
    r = c.patch("/hub/areas/den", headers=H, json={"labels": ["kids"]})
    assert r.json()["labels"] == ["kids"]
    hw = c.get("/hub/hardware/d1", headers=H).json()
    assert hw["labels"] == ["night_lights", "kids"]
    assert next(e for e in hw["entity_list"] if e["entity_id"] == "light.lamp")["labels"] == ["kids"]

    r = c.patch("/hub/labels/kids", headers=H, json={"name": "Children", "description": "Their rooms"})
    assert r.json()["name"] == "Children" and r.json()["label_id"] == "kids"
    r = c.delete("/hub/labels/kids", headers=H)
    assert r.json() == {"status": "removed", "label_id": "kids", "areas": 1, "devices": 1, "entities": 1}
    assert hub.devices["d1"].labels == ["night_lights"] and hub.entities["light.lamp"].labels == []
    assert next(d for d in store.load_devices() if d.id == "d1").labels == ["night_lights"]
    assert [x["label_id"] for x in c.get("/hub/labels", headers=H).json()["labels"]] == ["night_lights"]
    assert c.delete("/hub/labels/kids", headers=H).status_code == 404


def test_zones_publish_entities_a_zone_trigger_can_read(env):
    hub, store, rt, c = env
    registries.apply_zones(rt)
    assert hub.states.get("zone.home") is not None
    r = c.post("/hub/zones", headers=H, json={"name": "Practice Field", "latitude": 10.01, "longitude": 20.02,
                                              "radius": 250, "passive": False})
    assert r.status_code == 200, r.text
    zone = r.json()
    assert zone["entity_id"] == "zone.practice_field" and zone["editable"] is True
    st = hub.states.get("zone.practice_field")
    assert st.attributes["latitude"] == 10.01 and st.attributes["radius"] == 250
    # A fix inside it is in it, as the zone trigger and the trackers see it.
    assert mobile_app.zone_for(hub, 10.01, 20.02) == "Practice Field"

    for bad in ({"name": "X", "latitude": 91, "longitude": 0}, {"name": "X", "latitude": 0},
                {"name": "X", "latitude": 0, "longitude": 0, "radius": 0},
                {"name": "X", "latitude": 0, "longitude": 0, "passive": "no"},
                {"name": "X", "latitude": 0, "longitude": 0, "colour": "red"}):
        assert c.post("/hub/zones", headers=H, json=bad).status_code == 400, bad
    assert c.post("/hub/zones", headers=H, json={"name": "home", "latitude": 1, "longitude": 1}).status_code == 409

    # A rename keeps the entity id, so automations naming it keep working.
    r = c.patch(f"/hub/zones/{zone['id']}", headers=H, json={"name": "Ball Field", "radius": 300})
    assert r.json()["entity_id"] == "zone.practice_field"
    assert hub.states.get("zone.practice_field").attributes["friendly_name"] == "Ball Field"
    assert c.delete("/hub/zones/nope", headers=H).status_code == 404

    names = [z["name"] for z in c.get("/hub/zones", headers=H).json()["zones"]]
    assert names == ["Home", "Ball Field"]
    r = c.delete(f"/hub/zones/{zone['id']}", headers=H)
    assert r.json()["entity_id"] == "zone.practice_field"
    assert hub.states.get("zone.practice_field") is None and "zone.practice_field" not in hub.entities
    assert store.get_kv("zones") == []


MEMBER = {"Authorization": "Bearer member-token"}
ADMIN_TOKEN = {"Authorization": "Bearer admin-token"}


class _AdminAuth(Auth):
    """A parent's own hub login: an admin, but not the stack's internal secret."""

    def authenticate(self, token):
        if token == "admin-token":
            return Principal(user_id="u-admin", name="Parent", is_admin=True)
        return super().authenticate(token)


def test_a_member_cannot_add_change_or_remove_a_place_or_move_home(env):
    hub, store, rt, c = env
    zone = c.post("/hub/zones", headers=H, json={"name": "Pond", "latitude": 10.1, "longitude": 20.1}).json()
    m = TestClient(build_app(rt, _MemberAuth(store, SECRET)))
    assert m.get("/hub/zones", headers=MEMBER).status_code == 401
    assert m.post("/hub/zones", headers=MEMBER, json={"name": "Fort", "latitude": 1, "longitude": 1}).status_code == 401
    assert m.patch(f"/hub/zones/{zone['id']}", headers=MEMBER, json={"name": "Lake"}).status_code == 401
    assert m.delete(f"/hub/zones/{zone['id']}", headers=MEMBER).status_code == 401
    assert m.patch("/hub/zones/home", headers=MEMBER, json={"latitude": 11.0, "longitude": 21.0}).status_code == 401
    assert m.post("/hub/zones/import", headers=MEMBER, json={"places": []}).status_code == 401
    assert hub.location["latitude"] == 10.0 and [z["name"] for z in store.get_kv("zones")] == ["Pond"]


def test_home_moves_and_resizes_at_once_and_is_kept(env):
    hub, store, rt, c = env
    registries.apply_zones(rt)
    r = c.patch("/hub/zones/home", headers=H, json={"latitude": 11.5, "longitude": 21.5, "radius": 250})
    assert r.status_code == 200, r.text
    home = r.json()
    assert home["source"] == "home" and home["editable"] is True and home["removable"] is False
    assert (home["latitude"], home["longitude"], home["radius"]) == (11.5, 21.5, 250.0)
    # zone.home, the trackers and HA's /api/config follow at once...
    st = hub.states.get("zone.home")
    assert (st.attributes["latitude"], st.attributes["longitude"], st.attributes["radius"]) == (11.5, 21.5, 250.0)
    assert mobile_app.zone_for(hub, 11.5, 21.5) == "home" and mobile_app.zone_for(hub, 10.0, 20.0) == "not_home"
    assert c.get("/api/config", headers=H).json()["latitude"] == 11.5
    # ...and it is saved: the next start reads the same Home.
    assert store.get_kv("core_config")["latitude"] == 11.5 and store.get_kv("core_config")["radius"] == 250.0
    # A rename, and the place settings Home carries like any place.
    r = c.patch("/hub/zones/home", headers=H, json={"name": "Maple House", "important": True,
                                                    "notify": ["Alex"], "pin": "home"})
    assert r.json()["name"] == "Maple House" and r.json()["notify"] == ["alex"] and r.json()["important"] is True
    assert c.get("/hub/zones/home", headers=H).json()["pin"] == "home"
    # Home is never removed, and a bad move says why.
    r = c.delete("/hub/zones/home", headers=H)
    assert r.status_code == 409 and "cannot be removed" in r.json()["message"]
    for bad in ({"latitude": 95}, {"radius": 0}, {"passive": True}, {"pin": "castle"}, {"notify": "alex"}):
        assert c.patch("/hub/zones/home", headers=H, json=bad).status_code == 400, bad
    assert store.get_kv("core_config")["latitude"] == 11.5


async def test_home_moves_over_the_ha_websocket_too(env):
    from services.homehub import ha_ws_compat

    hub, store, rt, _c = env
    registries.apply_zones(rt)
    sent = []

    class Conn:
        runtime = rt
        principal = Principal(user_id="u-admin", name="Admin", is_admin=True)

        async def result(self, mid, value=None):
            sent.append(("result", mid, value))

        async def error(self, mid, code, message):
            sent.append(("error", mid, code))

    conn = Conn()
    await ha_ws_compat.core_update(conn, 1, {"id": 1, "type": "config/core/update", "latitude": 12.0,
                                             "longitude": 22.0, "radius": 120, "location_name": "Cottage"})
    assert sent[-1] == ("result", 1, None)
    assert hub.states.get("zone.home").attributes["latitude"] == 12.0
    assert store.get_kv("core_config")["location_name"] == "Cottage"
    await ha_ws_compat.core_update(conn, 2, {"id": 2, "type": "config/core/update", "time_zone": "UTC"})
    assert sent[-1] == ("error", 2, "not_supported")
    conn.principal = Principal(user_id="u-kid", name="Kid", is_admin=False)
    await ha_ws_compat.core_update(conn, 3, {"id": 3, "type": "config/core/update", "latitude": 1.0})
    assert sent[-1] == ("error", 3, "unauthorized") and hub.location["latitude"] == 12.0


def _place(c, viewer, **body):
    return c.post("/hub/zones", headers=H, params={"viewer": viewer},
                  json={"latitude": 10.2, "longitude": 20.2, **body})


def _names(c, **params):
    return [z["name"] for z in c.get("/hub/zones", headers=H, params=params).json()["zones"]]


def test_a_private_place_is_its_owners_alone(env):
    hub, store, rt, c = env
    registries.apply_zones(rt)
    r = _place(c, "sam", name="Hideout", owner="sam", visibility="private", important=True, notify=["kim"],
               pin="friends")
    assert r.status_code == 200, r.text
    hideout = r.json()
    assert hideout["entity_id"] == f"place.{hideout['id']}" and hideout["visibility"] == "private"
    # Never a household zone: no state for HA clients, no registry row, no tracker showing its name.
    assert not [s for s in hub.states.all("zone") if s.attributes.get("friendly_name") == "Hideout"]
    assert not [e for e in hub.entities.values() if e.unique_id == hideout["id"]]
    assert mobile_app.zone_for(hub, 10.2, 20.2) == "not_home"
    assert "Hideout" not in c.get("/api/states", headers=H).text
    # Its owner sees it; another person and the bare list do not.
    assert "Hideout" in _names(c, viewer="sam")
    assert "Hideout" not in _names(c, viewer="kim") and "Hideout" not in _names(c)
    # A parent's own hub login does not either: viewer and all count only for the stack's own secret.
    a = TestClient(build_app(rt, _AdminAuth(store, SECRET)))
    listed = a.get("/hub/zones", headers=ADMIN_TOKEN, params={"viewer": "sam", "all": "1"}).json()["zones"]
    assert "Hideout" not in [z["name"] for z in listed]
    assert a.get(f"/hub/zones/{hideout['id']}", headers=ADMIN_TOKEN).status_code == 404
    assert a.delete(f"/hub/zones/{hideout['id']}", headers=ADMIN_TOKEN).status_code == 404
    # A hub login's own private place is its own (a standalone hub has no Jarvis): Sam's list never shows it.
    own = a.post("/hub/zones", headers=ADMIN_TOKEN, json={"name": "Workshop", "latitude": 1, "longitude": 1,
                                                          "visibility": "private"})
    assert own.status_code == 200 and own.json()["owner"] == "u-admin"
    assert "Workshop" in [z["name"] for z in a.get("/hub/zones", headers=ADMIN_TOKEN).json()["zones"]]
    assert "Workshop" not in _names(c, viewer="sam")
    assert a.post("/hub/zones", headers=ADMIN_TOKEN, json={"name": "Y", "latitude": 1, "longitude": 1,
                                                           "owner": "sam"}).status_code == 400
    # Someone else cannot change or remove it (it is not there for them), and its name blocks nobody else.
    assert c.patch(f"/hub/zones/{hideout['id']}", headers=H, params={"viewer": "kim"},
                   json={"name": "Mine"}).status_code == 404
    assert c.delete(f"/hub/zones/{hideout['id']}", headers=H, params={"viewer": "kim"}).status_code == 404
    assert _place(c, "kim", name="Hideout", owner="kim", visibility="private").status_code == 200
    assert _place(c, "sam", name="Hideout", owner="sam", visibility="private").status_code == 409
    # Geo's presence reads every place.
    assert _names(c, all="1").count("Hideout") == 2
    # Shared with the family later: a household zone now, under a zone.* entity; private again: gone from HA.
    r = c.patch(f"/hub/zones/{hideout['id']}", headers=H, params={"viewer": "sam"},
                json={"visibility": "family", "name": "Fort"})
    assert r.json()["entity_id"] == "zone.fort" and hub.states.get("zone.fort") is not None
    r = c.patch(f"/hub/zones/{hideout['id']}", headers=H, params={"viewer": "sam"}, json={"visibility": "private"})
    assert r.json()["entity_id"].startswith("place.") and hub.states.get("zone.fort") is None
    assert c.delete(f"/hub/zones/{hideout['id']}", headers=H, params={"viewer": "sam"}).status_code == 200
    # A private place must say whose it is.
    assert _place(c, "sam", name="Nobody's", visibility="private").status_code == 400


def test_places_carry_their_owner_and_settings(env):
    hub, _store, rt, c = env
    registries.apply_zones(rt)
    place = _place(c, "sam", name="Library", owner="sam", visibility="family", important=True,
                   notify=["kim", "Sam"], pin="school").json()
    assert (place["owner_name"], place["important"], place["notify"], place["pin"]) == (
        "sam", True, ["kim", "sam"], "school")
    assert place["entity_id"] == "zone.library" and hub.states.get("zone.library") is not None
    assert c.get(f"/hub/zones/{place['id']}", headers=H).json()["name"] == "Library"
    assert c.post("/hub/zones", headers=H, json={"name": "Bad", "latitude": 1, "longitude": 1,
                                                 "owner": "Not A Name!"}).status_code == 400


MIRROR = [{"id": "geo_" + "a" * 32, "name": "Grandma's", "latitude": 10.5, "longitude": 20.5, "radius": 200.0,
           "passive": False, "icon": "mdi:map-marker-star", "source": "geo"}]
GEO_PLACES = [
    {"id": "a" * 32, "name": "Grandma's", "lat": 10.5, "lon": 20.5, "radius": 200.0, "scope": "family",
     "owner": "kim", "important": True, "notify": ["sam"], "icon": "friends", "created_at": 1700000000.0},
    {"id": "b" * 32, "name": "Secret spot", "lat": 10.6, "lon": 20.6, "radius": 80.0, "scope": "user",
     "owner": "sam", "important": False, "notify": [], "icon": "", "created_at": 1700000100.0},
    {"id": "not-a-place-id", "name": "Broken"},
]
GEO_SETTINGS = {"zone.home": {"important": True, "notify": ["kim", "sam"], "icon": ""},
                "zone.park": {"important": True, "notify": [], "icon": "park"},
                "zone.gone": {"important": True, "notify": [], "icon": ""}}


def test_the_mirror_of_saved_places_becomes_ordinary_zones_keeping_their_entity(env):
    hub, store, rt, c = env
    store.set_kv("geo_zones", MIRROR)
    old = EntityEntry("zone.grandma_s", "zone", "geo_" + "a" * 32, None, None)
    hub.entities[old.entity_id] = old
    store.save_entity(old)
    assert registries.adopt_mirrored_places(rt) == 1
    assert registries.adopt_mirrored_places(rt) == 0  # once
    registries.apply_zones(rt)
    zone = next(z for z in c.get("/hub/zones", headers=H).json()["zones"] if z["name"] == "Grandma's")
    assert zone["id"] == "a" * 32 and zone["entity_id"] == "zone.grandma_s" and zone["editable"] is True
    assert hub.states.get("zone.grandma_s") is not None and store.get_kv("geo_zones") == []


def test_geos_places_move_in_once_and_keep_their_ids(env):
    hub, store, rt, c = env
    store.set_kv("zones", [{"id": "park1", "name": "Park", "latitude": 10.3, "longitude": 20.3, "radius": 90}])
    park = EntityEntry("zone.park", "zone", "park1", None, None)
    hub.entities[park.entity_id] = park
    store.save_entity(park)
    store.set_kv("geo_zones", MIRROR)
    store.set_kv("users", [{"id": "hub-user-sam", "name": "Sam", "jarvis_user": "sam"}])
    old = EntityEntry("zone.grandma_s", "zone", "geo_" + "a" * 32, None, None)
    hub.entities[old.entity_id] = old
    store.save_entity(old)
    registries.adopt_mirrored_places(rt)
    r = c.post("/hub/zones/import", headers=H, json={"places": GEO_PLACES, "settings": GEO_SETTINGS})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["created"], out["kept"], out["skipped"], out["settings"]) == (1, 1, 1, 2)
    assert out["unmatched"] == ["zone.gone"]
    assert out["entity_ids"] == {"place." + "a" * 32: "zone.grandma_s", "place." + "b" * 32: "place." + "b" * 32}
    everything = {z["name"]: z for z in c.get("/hub/zones", headers=H, params={"all": "1"}).json()["zones"]}
    grandma, secret = everything["Grandma's"], everything["Secret spot"]
    assert (grandma["owner_name"], grandma["important"], grandma["notify"], grandma["pin"]) == (
        "kim", True, ["sam"], "friends")
    assert (secret["visibility"], secret["owner_name"], secret["created_at"]) == ("private", "sam", 1700000100.0)
    assert secret["owner"] == "hub-user-sam"  # Sam's hub user, from the hub's own records
    assert everything["Park"]["important"] is True and everything["Park"]["pin"] == "park"
    assert everything["Home"]["notify"] == ["kim", "sam"] and everything["Home"]["important"] is True
    assert hub.states.get("zone.grandma_s") is not None
    assert not [s for s in hub.states.all("zone") if s.attributes.get("friendly_name") == "Secret spot"]
    # Twice changes nothing, and a change made on the hub since is not overwritten.
    c.patch(f"/hub/zones/{grandma['id']}", headers=H, json={"important": False})
    again = c.post("/hub/zones/import", headers=H, json={"places": GEO_PLACES, "settings": GEO_SETTINGS}).json()
    assert (again["created"], again["kept"], again["settings"]) == (0, 2, 0)
    assert c.get(f"/hub/zones/{grandma['id']}", headers=H).json()["important"] is False
    assert len(store.get_kv("zones")) == 3
    # Only the stack's own services move places in.
    a = TestClient(build_app(rt, _AdminAuth(store, SECRET)))
    assert a.post("/hub/zones/import", headers=ADMIN_TOKEN, json={"places": []}).status_code == 403


async def test_persons_link_a_jarvis_user_and_trackers(env):
    hub, store, _rt, c = env
    await mobile_app.setup_persons(hub, [])
    listing = c.get("/hub/persons", headers=H).json()
    assert {t["entity_id"] for t in listing["trackers"]} == {"device_tracker.pixel", "device_tracker.tablet"}

    r = c.post("/hub/persons", headers=H, json={"name": "Sam Example", "jarvis_user": "Sam",
                                                "device_trackers": ["device_tracker.pixel"]})
    assert r.status_code == 200, r.text
    sam = r.json()
    assert sam["entity_id"] == "person.sam_example" and sam["jarvis_user"] == "sam"
    hub.states.set("device_tracker.pixel", "home", {"source_type": "router"})
    assert hub.states.get("person.sam_example").state == "home"

    assert c.post("/hub/persons", headers=H, json={"name": "Alex", "device_trackers": ["device_tracker.pixel"]}
                  ).status_code == 409  # one tracker, one person
    assert c.post("/hub/persons", headers=H, json={"name": "Alex", "jarvis_user": "sam"}).status_code == 409
    assert c.post("/hub/persons", headers=H, json={"name": "Alex", "device_trackers": ["light.lamp"]}
                  ).status_code == 400
    assert c.post("/hub/persons", headers=H, json={"name": "Alex", "jarvis_user": "no spaces"}).status_code == 400
    assert c.post("/hub/persons", headers=H, json={"name": "sam example"}).status_code == 409

    r = c.patch(f"/hub/persons/{sam['id']}", headers=H,
                json={"name": "Sam", "device_trackers": ["device_tracker.pixel", "device_tracker.tablet"]})
    assert r.json()["entity_id"] == "person.sam_example" and len(r.json()["device_trackers"]) == 2
    assert hub.person_manager.person_for_tracker("device_tracker.tablet")["name"] == "Sam"
    assert store.get_kv("persons")[0]["jarvis_user"] == "sam"

    r = c.delete(f"/hub/persons/{sam['id']}", headers=H)
    assert r.json()["entity_id"] == "person.sam_example"
    assert hub.states.get("person.sam_example") is None and hub.person_manager.persons == []
    assert c.delete(f"/hub/persons/{sam['id']}", headers=H).status_code == 404


async def test_a_person_without_trackers_follows_their_linked_jarvis_user(env):
    hub, _store, _rt, _c = env
    mgr = await mobile_app.setup_persons(hub, [{"id": "f00d", "name": "Robin", "jarvis_user": "robin"}])
    assert mgr.persons[0]["jarvis_user"] == "robin"
    mgr.update_from_fix(mgr.persons[0], {"latitude": 10.0, "longitude": 20.0, "gps_accuracy": 5})
    assert hub.states.get("person.robin").state == "home"


def test_the_import_brings_labels_categories_and_tags(tmp_path):
    cfg = tmp_path / "ha"
    (cfg / ".storage").mkdir(parents=True)

    def put(name, data):
        (cfg / ".storage" / name).write_text(json.dumps({"version": 1, "data": data}))

    put("core.label_registry", {"labels": [{"label_id": "outdoor", "name": "Outdoor", "color": "green",
                                            "icon": "mdi:tree", "description": None, "created_at": "x"}]})
    put("core.category_registry", {"categories": {"automation": [{"category_id": "c1", "name": "Lights",
                                                                  "icon": None}]}})
    put("tag", {"items": [{"id": "tag-1", "name": "Front door tag", "last_scanned": "2026-01-01T00:00:00+00:00"}]})
    put("core.floor_registry", {"floors": [{"floor_id": "ground", "name": "Ground", "level": 0}]})
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    report = import_config_dir(store, str(cfg))
    assert report["labels"] == 1 and report["tags"] == 1 and report["categories"] == 1 and report["floors"] == 1
    assert store.get_kv("labels") == {"outdoor": {"label_id": "outdoor", "name": "Outdoor", "color": "green",
                                                  "icon": "mdi:tree", "description": None}}
    hub = Hub(time_zone="UTC")
    rt = FakeRuntime(hub, store)
    rt.load_registry()
    assert registries.list_tags(rt) == {"tags": [{"id": "tag-1", "name": "Front door tag",
                                                  "last_scanned": "2026-01-01T00:00:00+00:00"}]}
    assert registries.list_categories(rt)["categories"]["automation"][0]["name"] == "Lights"
    assert registries.list_floors(rt)["floors"][0]["floor_id"] == "ground"


def test_a_jarvis_username_is_matched_to_the_hubs_own_user_without_asking_jarvis(env):
    _hub, store, rt, c = env
    assert registries.hub_user_for(rt, "kim") is None
    store.set_kv("persons", [{"id": "p1", "name": "Kim", "user_id": "hub-user-kim", "jarvis_user": "kim"}])
    assert registries.hub_user_for(rt, "Kim") == "hub-user-kim"
    store.set_kv("users", [{"id": "hub-user-kim-2", "name": "Kim", "jarvis_user": "kim"}])
    assert registries.hub_user_for(rt, "kim") == "hub-user-kim-2"  # a user synced from Jarvis wins
    place = _place(c, "kim", name="Studio", owner="kim", visibility="private").json()
    assert (place["owner"], place["owner_name"]) == ("hub-user-kim-2", "kim")
    # Kim's own hub login sees it too, since it is the same hub user.

    class KimAuth(Auth):
        def authenticate(self, token):
            if token == "kim-token":
                return Principal(user_id="hub-user-kim-2", name="Kim", is_admin=True)
            return super().authenticate(token)

    k = TestClient(build_app(rt, KimAuth(store, SECRET)))
    assert "Studio" in [z["name"] for z in k.get("/hub/zones", headers={"Authorization": "Bearer kim-token"})
                        .json()["zones"]]
