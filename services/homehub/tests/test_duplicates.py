"""Hiding duplicate devices (duplicates.py): the same bulb seen by two integrations, one copy hidden.

Every name, MAC and id here is made up.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import duplicates, entries, exposure, templating
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import Area, ConfigEntry, DeviceEntry, Driver, EntityEntry, Hub
from services.homehub.dashboard_api import rooms
from services.homehub.runtime import Runtime
from services.homehub.store import Store
from services.homehub.tests.test_flows import FakeRuntime

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}


class Bulbs(Driver):
    poll_interval = None

    def __init__(self, hub, entry):
        super().__init__(hub, entry)
        self.calls: list[tuple[str, str]] = []

    async def async_call(self, entity, domain, service, data):
        self.calls.append((entity.entity_id, service))
        self.publish(entity, "on" if service == "turn_on" else "off", {})


def _add(hub: Hub, store: Store, entry: ConfigEntry, devices: list[DeviceEntry], ents: list[EntityEntry]) -> None:
    hub.config_entries[entry.entry_id] = entry
    store.save_config_entry(entry)
    for d in devices:
        hub.devices[d.id] = d
        store.save_device(d)
    for e in ents:
        hub.entities[e.entity_id] = e
        store.save_entity(e)
        hub.states.set(e.entity_id, "off", {"friendly_name": e.entity_id})


@pytest.fixture
def env(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7")
    hub.store = store
    for a in (Area("pantry", "Pantry"), Area("study", "Study")):
        hub.areas[a.area_id] = a
        store.save_area(a)
    rt = FakeRuntime(hub, store)
    # Two lamps the fabric sees (no MAC) and the LAN sees (MAC), plus one fabric-only plug.
    _add(hub, store, ConfigEntry("mt", "matter", "Matter"),
         [DeviceEntry("m1", name="Pantry Lamp 1", area_id="pantry", config_entries=["mt"],
                      identifiers=[["matter", "node-1"]]),
          DeviceEntry("m2", name="Pantry Lamp 2", area_id="pantry", config_entries=["mt"],
                      identifiers=[["matter", "node-2"]]),
          DeviceEntry("m3", name="Study Plug", area_id="study", config_entries=["mt"])],
         [EntityEntry("light.pantry_lamp_1", "matter", "m-1", "mt", "m1"),
          EntityEntry("light.pantry_lamp_2", "matter", "m-2", "mt", "m2"),
          EntityEntry("switch.study_plug", "matter", "m-3", "mt", "m3")])
    _add(hub, store, ConfigEntry("wz", "wiz", "WiZ"),
         [DeviceEntry("w1", name="Pantry Lamp 1", area_id="pantry", config_entries=["wz"],
                      connections=[["mac", "a4:c1:38:00:00:01"]]),
          DeviceEntry("w2", name="pantry lamp  2", area_id="pantry", config_entries=["wz"],
                      connections=[["mac", "a4:c1:38:00:00:02"]])],
         [EntityEntry("light.pantry_lamp_1_wiz", "wiz", "a4c138000001", "wz", "w1"),
          EntityEntry("light.pantry_lamp_2_wiz", "wiz", "a4c138000002", "wz", "w2")])
    return hub, store, rt


def _groups(rt) -> dict[frozenset, dict]:
    return {frozenset(d["id"] for d in g["devices"]): g for g in duplicates.list_duplicates(rt)["groups"]}


# -- detection ----------------------------------------------------------------------------------

def test_same_name_in_the_same_room_from_two_integrations_is_one_group(env):
    _hub, _store, rt = env
    out = duplicates.list_duplicates(rt)
    assert set(_groups(rt)) == {frozenset({"m1", "w1"}), frozenset({"m2", "w2"})}
    g = _groups(rt)[frozenset({"m1", "w1"})]
    assert g["match"] == "name" and g["domains"] == ["matter", "wiz"] and g["area"] == "Pantry"
    row = {d["id"]: d for d in g["devices"]}["m1"]
    assert row["label"] == "Matter" and row["entity_ids"] == ["light.pantry_lamp_1"]
    assert row["hidden"] is False and row["last_updated"]
    assert {i["domain"]: (i["groups"], i["hidden"]) for i in out["integrations"]} == {
        "matter": (2, 0), "wiz": (2, 0)}


def test_the_same_name_in_another_room_is_not_a_duplicate(env):
    hub, _store, rt = env
    hub.devices["w2"].area_id = "study"
    assert set(_groups(rt)) == {frozenset({"m1", "w1"})}


def test_a_shared_mac_groups_devices_whatever_their_names(env):
    hub, store, rt = env
    _add(hub, store, ConfigEntry("es", "esphome", "ESPHome"),
         [DeviceEntry("e1", name="Shelf Node", area_id="study", config_entries=["es"],
                      connections=[["mac", "A4-C1-38-00-00-01"]])], [])
    group = _groups(rt)[frozenset({"m1", "w1", "e1"})]
    assert group["match"] == "mac"


def test_two_different_macs_are_two_things_even_with_one_name(env):
    hub, store, rt = env
    _add(hub, store, ConfigEntry("es", "esphome", "ESPHome"),
         [DeviceEntry("e1", name="Pantry Lamp 1", area_id="pantry", config_entries=["es"],
                      connections=[["mac", "a4:c1:38:00:00:99"]])], [])
    assert not any({"e1", "w1"} <= ids for ids in _groups(rt))
    # The lamp with no MAC goes with one of them, never both.
    assert frozenset({"m1", "w1"}) in _groups(rt) or frozenset({"m1", "e1"}) in _groups(rt)


def test_one_integrations_devices_are_never_grouped_together(env):
    hub, store, rt = env
    # A second WiZ bulb with the first one's name, room and even MAC: still not grouped with it.
    _add(hub, store, ConfigEntry("wz2", "wiz", "WiZ 2"),
         [DeviceEntry("w9", name="Pantry Lamp 1", area_id="pantry", config_entries=["wz2"],
                      connections=[["mac", "a4:c1:38:00:00:01"]])], [])
    for ids in _groups(rt):
        domains = [hub.config_entries[hub.devices[i].config_entries[0]].domain for i in ids]
        assert len(domains) == len(set(domains))
    assert frozenset({"m1", "w1"}) in _groups(rt) or frozenset({"m1", "w9"}) in _groups(rt)


# -- hiding one device ---------------------------------------------------------------------------

def test_hiding_a_device_hides_its_entities_and_showing_it_restores_them(env):
    hub, _store, rt = env
    hub.entities["light.pantry_lamp_1"].hidden_by = None
    out = entries.update_device(rt, "m1", {"hidden": True})
    assert out["hidden"] is True and out["hidden_by"] == "user"
    assert hub.entities["light.pantry_lamp_1"].hidden_by == "device"
    assert hub.is_hidden("light.pantry_lamp_1") and not hub.is_hidden("light.pantry_lamp_1_wiz")
    entries.update_device(rt, "m1", {"hidden": False})
    assert hub.devices["m1"].hidden_by is None and hub.entities["light.pantry_lamp_1"].hidden_by is None


def test_an_entity_a_person_hid_on_its_own_stays_hidden_when_its_device_is_shown(env):
    hub, _store, rt = env
    hub.entities["light.pantry_lamp_1"].hidden_by = "user"
    duplicates.set_device_hidden(rt, "m1", True)
    duplicates.set_device_hidden(rt, "m1", False)
    assert hub.entities["light.pantry_lamp_1"].hidden_by == "user"


def test_hidden_must_be_a_boolean(env):
    _hub, _store, rt = env
    with pytest.raises(entries.EntryError, match="true or false"):
        entries.update_device(rt, "m1", {"hidden": "yes"})


def test_hidden_survives_a_restart(env):
    _hub, store, rt = env
    duplicates.set_device_hidden(rt, "m1", True)
    fresh = Runtime(Hub(time_zone="Etc/GMT+7"), store)
    fresh.load_registry()
    assert fresh.hub.devices["m1"].hidden_by == "user"
    assert fresh.hub.entities["light.pantry_lamp_1"].hidden_by == "device"
    assert fresh.hub.is_hidden("light.pantry_lamp_1")


def test_a_resync_neither_clears_it_nor_leaves_a_new_entity_showing(env):
    hub, _store, rt = env
    duplicates.set_device_hidden(rt, "m1", True)
    drv = Bulbs(hub, hub.config_entries["mt"])
    dev = drv.ensure_device("Pantry Lamp 1", identifiers=[["matter", "node-1"]], manufacturer="Maker")
    assert dev.id == "m1" and dev.hidden_by == "user"
    ent = drv.ensure_entity("sensor", "m-1-power", "Power", device=dev)
    assert ent.hidden_by == "device"
    # A record saved by another path without the flag is brought in line at the next start.
    hub.entities["light.pantry_lamp_1"].hidden_by = None
    assert hub.reconcile_hidden() == ["light.pantry_lamp_1"]


def test_a_reimport_of_home_assistant_keeps_it_hidden(env, tmp_path):
    import json

    from services.homehub import importer

    _hub, store, rt = env
    duplicates.set_device_hidden(rt, "m1", True)
    storage = tmp_path / "ha" / ".storage"
    storage.mkdir(parents=True)
    (storage / "core.device_registry").write_text(json.dumps({"data": {"devices": [
        {"id": "m1", "name": "Pantry Lamp 1", "config_entries": ["mt"], "area_id": "pantry",
         "identifiers": [], "connections": []}]}}))
    importer.import_config_dir(store, str(tmp_path / "ha"))
    assert {d.id: d.hidden_by for d in store.load_devices()}["m1"] == "user"


# -- a hidden device keeps working ---------------------------------------------------------------

async def test_a_hidden_device_is_still_controlled_by_its_id_and_its_device(env):
    hub, _store, rt = env
    drv = Bulbs(hub, hub.config_entries["mt"])
    hub.drivers["mt"] = drv
    hub.drivers["wz"] = wiz = Bulbs(hub, hub.config_entries["wz"])
    duplicates.set_device_hidden(rt, "m1", True)
    await hub.call_service("light", "turn_on", {"entity_id": "light.pantry_lamp_1"})
    await hub.call_service("light", "turn_off", {"target": {"device_id": "m1"}})
    assert drv.calls == [("light.pantry_lamp_1", "turn_on"), ("light.pantry_lamp_1", "turn_off")]
    assert hub.states.get("light.pantry_lamp_1").state == "off"
    # A whole room reaches each bulb once, through the copy that is shown.
    drv.calls.clear()
    await hub.call_service("light", "turn_on", {"area_id": "pantry"})
    assert drv.calls == [("light.pantry_lamp_2", "turn_on")]
    assert sorted(wiz.calls) == [("light.pantry_lamp_1_wiz", "turn_on"), ("light.pantry_lamp_2_wiz", "turn_on")]


async def test_an_automation_naming_a_hidden_entity_still_runs():
    import asyncio

    from services.homehub.automation import AutomationEngine
    from services.homehub.tests.test_automation import START, FakeClock, FakeStore, auto, run, settle

    hub = Hub(time_zone="Etc/GMT+7")
    entry = ConfigEntry("mt", "matter", "Matter")
    hub.config_entries["mt"] = entry
    hub.devices["m1"] = DeviceEntry("m1", name="Porch Lamp", config_entries=["mt"], hidden_by="user")
    hub.entities["light.porch_lamp"] = EntityEntry("light.porch_lamp", "matter", "m-1", "mt", "m1",
                                                   hidden_by="device")
    hub.entities["binary_sensor.porch_motion"] = EntityEntry("binary_sensor.porch_motion", "matter", "m-2", "mt",
                                                             "m1", hidden_by="device")
    hub.drivers["mt"] = drv = Bulbs(hub, entry)
    hub.states.set("binary_sensor.porch_motion", "off", {})
    eng = AutomationEngine(hub, FakeStore(), clock=FakeClock(START))
    try:
        await run(eng, [auto([{"trigger": "state", "entity_id": "binary_sensor.porch_motion", "to": "on"}],
                             [{"action": "light.turn_on", "target": {"entity_id": "light.porch_lamp"}}])])
        hub.states.set("binary_sensor.porch_motion", "on", {})
        await settle()
        await asyncio.sleep(0)
        assert drv.calls == [("light.porch_lamp", "turn_on")]
    finally:
        await eng.stop()


# -- what browses leaves it out --------------------------------------------------------------------

def test_browsing_leaves_hidden_entities_out(env):
    hub, _store, rt = env
    duplicates.set_device_hidden(rt, "m1", True)
    # Rooms dashboard
    shown = [e["entity_id"] for a in rooms(hub, {})["areas"] for e in a["entities"]]
    assert "light.pantry_lamp_1" not in shown and "light.pantry_lamp_1_wiz" in shown
    # The voice exposure list. Asked by id, HA's rule: hidden is not exposed by default, a person's choice wins.
    assert "light.pantry_lamp_1" not in exposure.exposed_entities(hub)
    assert "light.pantry_lamp_1_wiz" in exposure.exposed_entities(hub)
    assert exposure.is_exposed(hub, "light.pantry_lamp_1") is False
    hub.entities["light.pantry_lamp_1"].options = {exposure.ASSISTANT: {"should_expose": True}}
    assert exposure.is_exposed(hub, "light.pantry_lamp_1") is True
    assert "light.pantry_lamp_1" not in exposure.exposed_entities(hub)
    # HA's template test
    assert str(templating.render(hub, "{{ is_hidden_entity('light.pantry_lamp_1') }}")) == "True"
    assert str(templating.render(hub, "{{ states.light | map(attribute='entity_id') | reject('is_hidden_entity')"
                                      " | list | count }}")) == "3"


def test_the_api(env):
    _hub, store, rt = env
    c = TestClient(build_app(rt, Auth(store, SECRET)))
    assert c.get("/hub/devices/duplicates").status_code == 401
    assert c.post("/hub/devices/duplicates/hide", json={"hide_domain": "matter"}).status_code == 401
    groups = c.get("/hub/devices/duplicates", headers=H).json()["groups"]
    assert len(groups) == 2

    r = c.patch("/hub/devices/w1", json={"hidden": True}, headers=H)
    assert r.status_code == 200 and r.json()["hidden"] is True
    assert c.patch("/hub/devices/w1", json={"hidden": 1}, headers=H).status_code == 400

    # Registry listings, HA-shaped, say so too.
    devs = {d["id"]: d for d in c.get("/api/config/device_registry/list", headers=H).json()}
    assert devs["w1"]["hidden_by"] == "user" and devs["m1"]["hidden_by"] is None
    ents = {e["entity_id"]: e for e in c.get("/api/config/entity_registry/list", headers=H).json()}
    assert ents["light.pantry_lamp_1_wiz"]["hidden_by"] == "device"
    assert c.get("/api/homehub/hidden_entities", headers=H).json() == {"entity_ids": ["light.pantry_lamp_1_wiz"]}
    hw = {d["id"]: d for d in c.get("/hub/hardware", headers=H).json()["devices"]}
    assert hw["w1"]["hidden"] is True and hw["m1"]["hidden"] is False
    assert c.patch("/hub/devices/w1", json={"hidden": False}, headers=H).json()["hidden"] is False


# -- the bulk action ---------------------------------------------------------------------------------

def test_hide_one_integrations_copies_and_show_them_again(env):
    hub, _store, rt = env
    out = duplicates.hide_copies(rt, {"hide_domain": "matter"})
    assert sorted(out["changed"]) == ["m1", "m2"]
    assert hub.devices["m3"].hidden_by is None  # not a duplicate: untouched
    assert all(not hub.devices[i].hidden_by for i in ("w1", "w2"))
    assert {i["domain"]: i["hidden"] for i in out["integrations"]} == {"matter": 2, "wiz": 0}
    # Again: nothing more to do.
    assert duplicates.hide_copies(rt, {"hide_domain": "matter"})["changed"] == []
    back = duplicates.show_copies(rt, {"domain": "matter"})
    assert sorted(back["changed"]) == ["m1", "m2"]
    assert not any(d.hidden_by for d in hub.devices.values())
    assert not any(e.hidden_by for e in hub.entities.values())


def test_keep_one_integration_hides_the_others(env):
    hub, store, rt = env
    _add(hub, store, ConfigEntry("es", "esphome", "ESPHome"),
         [DeviceEntry("e1", name="Pantry Lamp 2", area_id="pantry", config_entries=["es"])], [])
    out = duplicates.hide_copies(rt, {"keep_domain": "wiz"})
    assert sorted(out["changed"]) == ["e1", "m1", "m2"]
    # Both named: only groups holding both, and only the named integration's copies.
    duplicates.show_copies(rt, {"domain": "matter"})
    duplicates.show_copies(rt, {"domain": "esphome"})
    assert duplicates.hide_copies(rt, {"hide_domain": "esphome", "keep_domain": "matter"})["changed"] == ["e1"]


@pytest.mark.parametrize("body", [{}, {"hide_domain": ""}, {"hide_domain": "wiz", "keep_domain": "wiz"},
                                  {"keep_domain": 3}])
def test_the_bulk_action_says_what_it_needs(env, body):
    _hub, _store, rt = env
    with pytest.raises(entries.EntryError):
        duplicates.hide_copies(rt, body)


def test_the_bulk_routes(env):
    _hub, store, rt = env
    c = TestClient(build_app(rt, Auth(store, SECRET)))
    r = c.post("/hub/devices/duplicates/hide", json={"keep_domain": "wiz"}, headers=H)
    assert r.status_code == 200 and sorted(r.json()["changed"]) == ["m1", "m2"]
    assert c.post("/hub/devices/duplicates/hide", json={}, headers=H).status_code == 400
    r = c.post("/hub/devices/duplicates/unhide", json={"domain": "matter"}, headers=H)
    assert sorted(r.json()["changed"]) == ["m1", "m2"]
    assert c.post("/hub/devices/duplicates/unhide", json={}, headers=H).status_code == 400
