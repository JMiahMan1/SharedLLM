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
    assert c.post("/hub/zones/sync").status_code == 401


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
    assert c.patch("/hub/zones/home", headers=H, json={"name": "Y"}).status_code == 409
    assert c.delete("/hub/zones/nope", headers=H).status_code == 404

    names = [z["name"] for z in c.get("/hub/zones", headers=H).json()["zones"]]
    assert names == ["Home", "Ball Field"]
    r = c.delete(f"/hub/zones/{zone['id']}", headers=H)
    assert r.json()["entity_id"] == "zone.practice_field"
    assert hub.states.get("zone.practice_field") is None and "zone.practice_field" not in hub.entities
    assert store.get_kv("zones") == []


GEO = {"type": "FeatureCollection", "features": [
    {"geometry": {"coordinates": [20.5, 10.5]}, "properties": {
        "entity_id": "place." + "a" * 32, "friendly_name": "Grandma's", "radius": 200, "custom": True,
        "scope": "family"}},
    {"geometry": {"coordinates": [20.6, 10.6]}, "properties": {
        "entity_id": "place." + "b" * 32, "friendly_name": "Secret spot", "radius": 80, "custom": True,
        "scope": "user", "owner": "sam"}},
    {"geometry": {"coordinates": [20.0, 10.0]}, "properties": {"entity_id": "zone.home", "state": "0"}},
]}


async def test_family_saved_places_become_read_only_zones(env, monkeypatch):
    hub, store, rt, c = env

    async def fetch(base):
        assert base == "http://geo.test"
        return GEO

    monkeypatch.setattr(registries, "fetch_places", fetch)
    result = await registries.sync_places(rt, "http://geo.test")
    assert result == {"places": 1, "added": 1, "removed": 0}
    st = hub.states.get("zone.grandma_s")
    assert st is not None and st.attributes["source"] == "geo" and st.attributes["editable"] is False
    assert mobile_app.zone_for(hub, 10.5, 20.5) == "Grandma's"
    zones = c.get("/hub/zones", headers=H).json()["zones"]
    mirrored = next(z for z in zones if z["source"] == "geo")
    assert mirrored["editable"] is False and "Secret spot" not in [z["name"] for z in zones]
    assert c.patch(f"/hub/zones/{mirrored['id']}", headers=H, json={"name": "X"}).status_code == 409
    assert c.delete(f"/hub/zones/{mirrored['id']}", headers=H).status_code == 409

    # The place is removed in Jarvis: its zone goes too.
    monkeypatch.setattr(registries, "fetch_places", lambda base: _async({"features": []}))
    assert (await registries.sync_places(rt, "http://geo.test"))["removed"] == 1
    assert hub.states.get("zone.grandma_s") is None and store.get_kv("geo_zones") == []


async def test_no_geo_url_is_said_not_guessed(env):
    _hub, _store, rt, _c = env
    with pytest.raises(registries.RegistryError) as err:
        await registries.sync_places(rt, "")
    assert err.value.status == 503 and "GEO_SVC_URL" in str(err.value)


async def _async(value):
    return value


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


async def test_saved_places_are_asked_for_with_the_internal_secret(monkeypatch):
    # Geo answers only callers with the stack's internal secret; without it every round was a 403.
    from aiohttp import web

    seen = []

    async def zones(request):
        seen.append(request.headers.get("X-Internal-Secret"))
        if request.headers.get("X-Internal-Secret") != "s3cret":
            return web.json_response({"detail": "Forbidden"}, status=403)
        return web.json_response({"features": []})

    app = web.Application()
    app.router.add_get("/zones", zones)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        monkeypatch.setattr(mobile_app, "_geo_settings", lambda: (f"http://127.0.0.1:{port}", "s3cret"))
        assert await registries.fetch_places(f"http://127.0.0.1:{port}") == {"features": []}
        assert seen == ["s3cret"]
        monkeypatch.setattr(mobile_app, "_geo_settings", lambda: (f"http://127.0.0.1:{port}", ""))
        with pytest.raises(registries.RegistryError, match="INTERNAL_SECRET"):
            await registries.fetch_places(f"http://127.0.0.1:{port}")
    finally:
        await runner.cleanup()
