"""Combining duplicates (duplicates.py): one device with a main and a fallback copy.

The shapes follow a real house (a bulb paired to the Matter fabric and found on the LAN by WiZ), but every
name, MAC and id here is made up.
"""
from __future__ import annotations

import asyncio
import base64
import logging

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import core, duplicates, entries, light_groups
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import (
    UNAVAILABLE,
    Area,
    ConfigEntry,
    DeviceEntry,
    DeviceUnavailable,
    Driver,
    EntityEntry,
    Hub,
)
from services.homehub.drivers.group import LightGroupDriver
from services.homehub.drivers.matter import node_device_info, node_macs
from services.homehub.runtime import Runtime
from services.homehub.store import Store
from services.homehub.tests.test_flows import FakeRuntime

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}
MAC = "d8:00:5e:00:00:01"  # universally administered MACs, made up
MAC2 = "d8:00:5e:00:00:02"
KITCHEN_MAC = "d8:00:5e:00:00:03"


class Bulbs(Driver):
    """A fake bulb driver: ``dead`` entities refuse, ``slow`` ones never answer."""

    poll_interval = None

    def __init__(self, hub, entry):
        super().__init__(hub, entry)
        self.calls: list[tuple[str, str]] = []
        self.dead: set[str] = set()
        self.slow: set[str] = set()

    async def async_call(self, entity, domain, service, data):
        self.calls.append((entity.entity_id, service))
        if entity.entity_id in self.dead:
            raise DeviceUnavailable(f"{entity.entity_id} did not answer")
        if entity.entity_id in self.slow:
            await asyncio.sleep(60)
        self.publish(entity, "on" if service == "turn_on" else "off",
                     {"brightness": data.get("brightness", 255)} if service == "turn_on" else {})


def _node(node_id: int, mac: str | None, *, label: str = "") -> dict:
    iface = {"0": "WIFI_STA_DEF", "1": True, "4": base64.b64encode(bytes.fromhex(mac.replace(":", ""))).decode()
             if mac else None, "5": ["wKgCDQ=="], "7": 1}
    return {"node_id": node_id, "available": True, "attributes": {
        "0/40/1": "Example Lighting", "0/40/3": "Example A19 Filament", "0/40/5": label,
        "0/51/0": [iface]}}


def _add(hub, store, entry, devices, ents):
    hub.config_entries[entry.entry_id] = entry
    store.save_config_entry(entry)
    for d in devices:
        hub.devices[d.id] = d
        store.save_device(d)
    for e in ents:
        hub.entities[e.entity_id] = e
        store.save_entity(e)


@pytest.fixture
def env(tmp_path):
    """Two bathroom bulbs (Matter names them, WiZ names them by MAC) and a kitchen bulb (one name on both)."""
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7")
    hub.store = store
    for a in (Area("bath", "Guest Bath"), Area("kitchen", "Kitchen")):
        hub.areas[a.area_id] = a
        store.save_area(a)
    rt = FakeRuntime(hub, store)
    _add(hub, store, ConfigEntry("mt", "matter", "Matter"),
         [DeviceEntry("m1", name="Example A19 Filament", name_by_user="Guest Bath 1", area_id="bath",
                      config_entries=["mt"], connections=[["mac", MAC]], labels=["lights"]),
          DeviceEntry("m2", name="Example A19 Filament", name_by_user="Guest Bath 2", area_id="bath",
                      config_entries=["mt"], connections=[["mac", MAC2]]),
          DeviceEntry("mk", name="Kitchen Light 1", name_by_user="Kitchen Light 1", area_id="kitchen",
                      config_entries=["mt"], hidden_by="user")],
         [EntityEntry("light.guest_bath_1_light", "matter", "m-1", "mt", "m1", name="Light", has_entity_name=True),
          EntityEntry("button.guest_bath_1_identify", "matter", "m-1-id", "mt", "m1", entity_category="config"),
          EntityEntry("light.guest_bath_2", "matter", "m-2", "mt", "m2", has_entity_name=True),
          EntityEntry("light.kitchen_light_1", "matter", "m-k", "mt", "mk", has_entity_name=True,
                      hidden_by="device")])
    _add(hub, store, ConfigEntry("wz", "wiz", "WiZ"),
         [DeviceEntry("w1", name="WiZ Tunable White 000001", config_entries=["wz"], connections=[["mac", MAC]]),
          DeviceEntry("w2", name="WiZ Tunable White 000002", area_id="bath", config_entries=["wz"],
                      connections=[["mac", MAC2]]),
          DeviceEntry("wk", name="WiZ RGBW Tunable 000003", name_by_user="Kitchen Light 1", area_id="kitchen",
                      config_entries=["wz"], connections=[["mac", KITCHEN_MAC]])],
         [EntityEntry("light.wiz_tunable_white_000001", "wiz", "w-1", "wz", "w1"),
          EntityEntry("sensor.wiz_tunable_white_000001_power", "wiz", "w-1-p", "wz", "w1"),
          EntityEntry("light.wiz_tunable_white_000002", "wiz", "w-2", "wz", "w2"),
          EntityEntry("light.wiz_rgbw_tunable_000003", "wiz", "w-k", "wz", "wk")])
    hub.drivers["mt"] = matter = Bulbs(hub, hub.config_entries["mt"])
    hub.drivers["wz"] = wiz = Bulbs(hub, hub.config_entries["wz"])
    for eid in hub.entities:
        if eid.startswith("light."):
            (matter if hub.entities[eid].platform == "matter" else wiz).publish(eid, "off", {})
    return hub, store, rt, matter, wiz


def _groups(rt):
    return {frozenset(d["id"] for d in g["devices"]): g for g in duplicates.list_duplicates(rt)["groups"]}


# -- pairing -------------------------------------------------------------------------------------------

def test_matter_reports_the_bulbs_wifi_mac():
    assert node_macs(_node(30, MAC)) == [MAC]
    info = node_device_info(0x1234, _node(30, MAC))
    assert info["connections"] == [["mac", MAC]]
    # No interface, a Thread extended address (8 bytes) or an all-zero placeholder is no MAC.
    assert node_macs({"node_id": 1, "attributes": {}}) == []
    thread = {"node_id": 2, "attributes": {"0/51/0": [{"4": base64.b64encode(bytes(range(1, 9))).decode()}]}}
    assert node_macs(thread) == []
    assert node_macs(_node(3, "00:00:00:00:00:00")) == []
    # A hex string (older servers) reads the same.
    assert node_macs({"node_id": 4, "attributes": {"0/51/0": [{"4": MAC.replace(":", "")}]}}) == [MAC]


def test_an_unset_node_label_is_not_a_name():
    info = node_device_info(0x1234, _node(30, MAC, label="\x00" * 32))
    assert info["name"] == "Example A19 Filament"


def test_copies_with_different_names_pair_on_their_mac(env):
    _hub, _store, rt, *_ = env
    groups = _groups(rt)
    assert groups[frozenset({"m1", "w1"})]["match"] == "mac"
    assert groups[frozenset({"m2", "w2"})]["match"] == "mac"
    assert groups[frozenset({"mk", "wk"})]["match"] == "name"
    assert groups[frozenset({"m1", "w1"})]["can_combine"] is True


def test_a_shared_serial_pairs_and_two_serials_never_do(env):
    hub, store, rt, *_ = env
    _add(hub, store, ConfigEntry("hk", "homekit_controller", "HomeKit"),
         [DeviceEntry("h1", name="Porch Plug", config_entries=["hk"], serial_number="SN-4471-AB")], [])
    _add(hub, store, ConfigEntry("sh", "shelly", "Shelly"),
         [DeviceEntry("s1", name="Plug S", config_entries=["sh"], serial_number="sn4471ab"),
          DeviceEntry("s2", name="Porch Plug", config_entries=["sh"], serial_number="SN-9999-ZZ")], [])
    groups = _groups(rt)
    assert groups[frozenset({"h1", "s1"})]["match"] == "serial"
    assert not any("s2" in ids for ids in groups)


def test_nothing_pairs_on_a_guess(env):
    hub, store, rt, *_ = env
    # Same room, no shared name, no shared MAC or serial: not a pair.
    _add(hub, store, ConfigEntry("es", "esphome", "ESPHome"),
         [DeviceEntry("e1", name="Shelf Light", area_id="bath", config_entries=["es"])], [])
    assert not any("e1" in ids for ids in _groups(rt))


# -- combining -------------------------------------------------------------------------------------------

def test_combining_with_matter_as_main_shows_one_light_with_its_name(env):
    hub, _store, rt, *_ = env
    out = duplicates.combine(rt, {"main": "m1", "fallback": "w1"})
    assert out["combined"] == ["m1"]
    g = _groups(rt)[frozenset({"m1", "w1"})]
    assert g["combined"]["main_label"] == "Matter" and g["combined"]["fallback_label"] == "WiZ"
    assert g["combined"]["entities"] == [{"main": "light.guest_bath_1_light",
                                          "fallback": "light.wiz_tunable_white_000001"}]
    assert {r["id"]: r["role"] for r in g["devices"]} == {"m1": "main", "w1": "fallback"}
    # One device shows; the fallback is kept, hidden, with every entity on it.
    assert hub.devices["w1"].hidden_by == "combined" and hub.devices["m1"].hidden_by is None
    assert hub.is_hidden("light.wiz_tunable_white_000001")
    assert hub.is_hidden("sensor.wiz_tunable_white_000001_power")
    assert not hub.is_hidden("light.guest_bath_1_light")
    # "Light" names nothing: the combined light takes the device's name.
    assert hub.states.get("light.guest_bath_1_light").attributes["friendly_name"] == "Guest Bath 1"
    assert hub.area_entities("bath").count("light.guest_bath_1_light") == 1
    # A combined copy is never hidden or shown on its own; the bulk hide leaves it alone.
    with pytest.raises(entries.EntryError, match="Uncombine"):
        duplicates.set_device_hidden(rt, "w1", False)
    with pytest.raises(entries.EntryError, match="Uncombine"):
        entries.update_device(rt, "w1", {"hidden": False, "name_by_user": "Oops"})
    assert hub.devices["w1"].name_by_user is None
    assert "w1" not in duplicates.show_copies(rt, {"domain": "wiz"})["changed"]
    assert "m1" not in duplicates.hide_copies(rt, {"hide_domain": "matter"})["changed"]


def test_combining_with_wiz_as_main_carries_the_name_and_room_over_and_uncombining_restores(env):
    hub, _store, rt, *_ = env
    duplicates.combine(rt, {"main": "w1", "fallback": "m1"})
    w1 = hub.devices["w1"]
    assert w1.name_by_user == "Guest Bath 1" and w1.area_id == "bath" and w1.labels == ["lights"]
    assert hub.states.get("light.wiz_tunable_white_000001").attributes["friendly_name"] == "Guest Bath 1"
    assert hub.entity_area_id("light.wiz_tunable_white_000001") == "bath"
    assert hub.devices["m1"].hidden_by == "combined"
    duplicates.uncombine(rt, {"device": "m1"})
    assert (w1.name_by_user, w1.area_id, w1.labels, w1.hidden_by) == (None, None, [], None)
    assert hub.devices["m1"].hidden_by is None
    assert not hub.is_hidden("light.guest_bath_1_light") and not hub.combined
    assert hub.states.get("light.wiz_tunable_white_000001").attributes["friendly_name"] == \
        "WiZ Tunable White 000001"


def test_uncombining_puts_a_hidden_copy_back_hidden_and_keeps_later_changes(env):
    hub, _store, rt, *_ = env
    # The kitchen: the Matter copy was hidden by hand; combining makes it the main and shows it.
    duplicates.combine(rt, {"main": "mk", "fallback": "wk"})
    assert hub.devices["mk"].hidden_by is None and not hub.is_hidden("light.kitchen_light_1")
    assert hub.devices["wk"].hidden_by == "combined"
    entries.update_device(rt, "mk", {"name_by_user": "Kitchen Pendant"})
    duplicates.uncombine(rt, {"device": "mk"})
    assert hub.devices["mk"].hidden_by == "user" and hub.entities["light.kitchen_light_1"].hidden_by == "device"
    assert hub.devices["wk"].hidden_by is None and not hub.is_hidden("light.wiz_rgbw_tunable_000003")
    assert hub.devices["mk"].name_by_user == "Kitchen Pendant"


def test_what_combining_refuses(env):
    _hub, _store, rt, *_ = env
    with pytest.raises(entries.EntryError, match="not copies"):
        duplicates.combine(rt, {"main": "m1", "fallback": "w2"})
    with pytest.raises(entries.EntryError, match="main and which is the fallback"):
        duplicates.combine(rt, {"main": "m1"})
    with pytest.raises(entries.EntryError, match="main"):
        duplicates.combine(rt, {"group": "m1"})  # no rule yet, none named
    duplicates.combine(rt, {"main": "m1", "fallback": "w1"})
    with pytest.raises(entries.EntryError, match="already combined"):
        duplicates.combine(rt, {"main": "w1", "fallback": "m1"})
    with pytest.raises(entries.EntryError, match="not combined"):
        duplicates.uncombine(rt, {"device": "m2"})


def test_the_household_rule_combines_all_like_this_and_new_pairs_follow_it(env):
    hub, _store, rt, *_ = env
    out = duplicates.combine(rt, {"main_domain": "matter", "fallback_domain": "wiz"})
    assert sorted(out["combined"]) == ["m1", "m2", "mk"]
    assert out["rules"] == [{"domains": ["matter", "wiz"], "main_domain": "matter", "main_label": "Matter",
                             "fallback_domain": "wiz", "fallback_label": "WiZ"}]
    assert all(hub.devices[i].hidden_by == "combined" for i in ("w1", "w2", "wk"))
    duplicates.uncombine(rt, {"device": "w2"})
    assert _groups(rt)[frozenset({"m2", "w2"})]["suggested_main"] == "matter"
    assert duplicates.combine(rt, {"group": "w2"})["combined"] == ["m2"]
    duplicates.set_rule(rt, {"clear": ["wiz", "matter"]})
    assert duplicates.list_duplicates(rt)["rules"] == []


def test_swapping_main_and_fallback(env):
    hub, _store, rt, *_ = env
    duplicates.combine(rt, {"main": "m1", "fallback": "w1"})
    out = duplicates.swap(rt, {"device": "w1"})
    assert out["combined"] == ["w1"]
    assert hub.devices["m1"].hidden_by == "combined" and hub.devices["w1"].hidden_by is None
    assert hub.combined["light.guest_bath_1_light"] == ("light.wiz_tunable_white_000001", "light.guest_bath_1_light")
    # The Matter light's "Light" name is back as it was, since Matter is no longer the main.
    assert hub.entities["light.guest_bath_1_light"].name == "Light"


# -- failover --------------------------------------------------------------------------------------------

async def test_commands_go_to_the_main_and_to_the_fallback_when_it_fails(env, caplog):
    hub, _store, rt, matter, wiz = env
    duplicates.combine(rt, {"main": "m1", "fallback": "w1"})
    await hub.call_service("light", "turn_on", {"entity_id": "light.guest_bath_1_light"})
    assert matter.calls[-1] == ("light.guest_bath_1_light", "turn_on") and wiz.calls == []
    matter.dead.add("light.guest_bath_1_light")
    with caplog.at_level(logging.INFO, logger="homehub"):
        await hub.call_service("light", "turn_off", {"entity_id": "light.guest_bath_1_light"})
    assert wiz.calls == [("light.wiz_tunable_white_000001", "turn_off")]
    assert any("used fallback light.wiz_tunable_white_000001" in r.getMessage() for r in caplog.records)
    # The main still says "on", but the fallback did the last command: its state is the true one...
    assert hub.states.get("light.guest_bath_1_light").state == "off"
    # ...until the main speaks again.
    matter.publish("light.guest_bath_1_light", "on", {})
    assert hub.states.get("light.guest_bath_1_light").state == "on"


async def test_a_main_that_does_not_answer_in_time_hands_over(env, monkeypatch):
    hub, _store, rt, matter, wiz = env
    monkeypatch.setattr(core, "COMBINED_MAIN_TIMEOUT_S", 0.05)
    duplicates.combine(rt, {"main": "m1", "fallback": "w1"})
    matter.slow.add("light.guest_bath_1_light")
    await hub.call_service("light", "turn_on", {"entity_id": "light.guest_bath_1_light"})
    assert wiz.calls == [("light.wiz_tunable_white_000001", "turn_on")]


async def test_a_slow_main_is_not_doubled_by_its_fallback_for_play_media(monkeypatch):
    """A Cast TV that switches app before loading took longer than the main's 6 s, the fallback played the
    same clip too, and the main's load finished anyway: every announcement was heard twice, seconds apart.
    play_media now waits for the main and moves on only when it fails."""
    monkeypatch.setattr(core, "COMBINED_MAIN_TIMEOUT_S", 0.05)

    class Player(Driver):
        poll_interval = None

        def __init__(self, hub, entry, delay):
            super().__init__(hub, entry)
            self.delay, self.calls = delay, []

        async def async_call(self, entity, domain, service, data):
            await asyncio.sleep(self.delay)
            self.calls.append((entity.entity_id, service))

    hub = Hub()
    drivers = {}
    for eid, entry_id, delay in (("media_player.tv_cast", "cast", 0.2), ("media_player.tv_ma", "ma", 0)):
        entry = ConfigEntry(entry_id=entry_id, domain=entry_id)
        hub.config_entries[entry_id] = entry
        hub.entities[eid] = EntityEntry(entity_id=eid, platform=entry_id, config_entry_id=entry_id)
        drivers[entry_id] = hub.drivers[entry_id] = Player(hub, entry, delay)
    pair = ("media_player.tv_cast", "media_player.tv_ma")
    hub.combined = {pair[0]: pair, pair[1]: pair}
    await hub.call_service("media_player", "play_media", {
        "entity_id": "media_player.tv_cast", "media_content_id": "http://x/clip.wav", "announce": True})
    assert drivers["cast"].calls == [("media_player.tv_cast", "play_media")] and drivers["ma"].calls == []
    await hub.call_service("media_player", "volume_set", {"entity_id": "media_player.tv_cast", "volume_level": 0.3})
    assert drivers["ma"].calls == [("media_player.tv_ma", "volume_set")]  # other commands still hand over


async def test_a_main_known_to_be_unavailable_is_skipped(env):
    hub, _store, rt, matter, wiz = env
    duplicates.combine(rt, {"main": "m1", "fallback": "w1"})
    matter.publish("light.guest_bath_1_light", UNAVAILABLE, {})
    matter.calls.clear()
    await hub.call_service("light", "turn_on", {"entity_id": "light.guest_bath_1_light"})
    assert matter.calls == [] and wiz.calls == [("light.wiz_tunable_white_000001", "turn_on")]


async def test_when_both_copies_fail_the_call_fails_with_both_reasons(env):
    hub, _store, rt, matter, wiz = env
    duplicates.combine(rt, {"main": "m1", "fallback": "w1"})
    matter.dead.add("light.guest_bath_1_light")
    wiz.dead.add("light.wiz_tunable_white_000001")
    with pytest.raises(DeviceUnavailable) as err:
        await hub.call_service("light", "turn_on", {"entity_id": "light.guest_bath_1_light"})
    assert "light.guest_bath_1_light" in str(err.value) and "light.wiz_tunable_white_000001" in str(err.value)


async def test_either_id_a_room_or_a_device_reaches_the_combined_light_once(env):
    hub, _store, rt, matter, wiz = env
    duplicates.combine(rt, {"main": "m1", "fallback": "w1"})
    # An automation written against the WiZ id still works, through the main first.
    await hub.call_service("light", "turn_on", {"entity_id": ["light.wiz_tunable_white_000001",
                                                              "light.guest_bath_1_light"]})
    assert matter.calls[-1:] == [("light.guest_bath_1_light", "turn_on")] and wiz.calls == []
    matter.calls.clear()
    await hub.call_service("light", "turn_off", {"area_id": "bath"})
    assert ("light.guest_bath_1_light", "turn_off") in matter.calls
    assert not any(c[0] == "light.wiz_tunable_white_000001" for c in wiz.calls)
    await hub.call_service("homeassistant", "turn_on", {"entity_id": "light.wiz_tunable_white_000001"})
    assert matter.calls[-1] == ("light.guest_bath_1_light", "turn_on")


async def test_a_light_group_of_the_old_ids_fails_over_too(env):
    hub, _store, rt, matter, wiz = env
    duplicates.combine(rt, {"main": "m1", "fallback": "w1"})
    out = await light_groups.create_group(rt, {"name": "Bath lights",
                                                  "entities": ["light.wiz_tunable_white_000001", "light.guest_bath_2"]})
    group = LightGroupDriver(hub, hub.config_entries[out["entry_id"]])
    hub.drivers[out["entry_id"]] = group
    await group.async_setup()
    matter.dead.add("light.guest_bath_1_light")
    await hub.call_service("light", "turn_on", {"entity_id": out["entity_id"]})
    assert wiz.calls == [("light.wiz_tunable_white_000001", "turn_on")]
    assert ("light.guest_bath_2", "turn_on") in matter.calls
    assert hub.states.get(out["entity_id"]).state == "on"


# -- state -----------------------------------------------------------------------------------------------

def test_state_comes_from_the_main_while_it_is_available_else_the_fallback(env):
    hub, _store, rt, matter, wiz = env
    duplicates.combine(rt, {"main": "m1", "fallback": "w1"})
    main, fb = "light.guest_bath_1_light", "light.wiz_tunable_white_000001"
    matter.publish(main, "on", {"brightness": 200})
    wiz.publish(fb, "off", {})
    assert hub.states.get(main).state == "on" and hub.states.get(main).attributes["brightness"] == 200
    assert hub.states.get(fb).state == "on"  # both ids are the one device
    matter.publish(main, UNAVAILABLE, {})
    assert hub.states.get(main).state == "off"
    assert hub.states.get(main).attributes["friendly_name"] == "Guest Bath 1"
    g = _groups(rt)[frozenset({"m1", "w1"})]["combined"]
    assert (g["main_available"], g["fallback_available"], g["using"]) == (False, True, "fallback")
    wiz.publish(fb, UNAVAILABLE, {})
    assert hub.states.get(main).state == UNAVAILABLE
    matter.publish(main, "on", {})
    assert hub.states.get(main).state == "on"
    duplicates.uncombine(rt, {"device": "m1"})
    assert hub.states.get(main).state == "on" and hub.states.get(fb).state == UNAVAILABLE


def test_a_combination_survives_a_restart(env):
    _hub, store, rt, *_ = env
    duplicates.combine(rt, {"main": "m1", "fallback": "w1"})
    fresh = Runtime(Hub(time_zone="Etc/GMT+7"), store)
    fresh.load_registry()
    assert fresh.hub.combined["light.wiz_tunable_white_000001"] == ("light.guest_bath_1_light",
                                                                    "light.wiz_tunable_white_000001")
    assert fresh.hub.devices["w1"].hidden_by == "combined"
    assert fresh.hub.is_hidden("sensor.wiz_tunable_white_000001_power")


def test_a_combination_whose_device_is_gone_is_dropped_at_start(env):
    _hub, store, rt, *_ = env
    duplicates.combine(rt, {"main": "w1", "fallback": "m1"})
    store._exec("DELETE FROM devices WHERE id=?", ("w1",))
    fresh = Runtime(Hub(time_zone="Etc/GMT+7"), store)
    fresh.load_registry()
    assert not fresh.hub.combined and fresh.hub.devices["m1"].hidden_by is None
    assert store.get_kv(duplicates.COMBINED_KEY) == []


async def test_removing_the_main_device_shows_the_fallback_again_and_drops_the_combination(env):
    hub, store, rt, *_ = env
    duplicates.combine(rt, {"main": "w1", "fallback": "m1"})
    assert hub.devices["m1"].hidden_by == "combined"
    await entries.remove_device(rt, "w1")
    assert "w1" not in hub.devices and store.get_kv(duplicates.COMBINED_KEY) == []
    assert not hub.combined and hub.devices["m1"].hidden_by is None
    assert not hub.is_hidden("light.guest_bath_1_light")
    assert duplicates.record_of(rt, "m1") is None


async def test_removing_the_fallbacks_integration_uncombines_and_restores_the_main(env):
    hub, store, rt, *_ = env
    duplicates.combine(rt, {"main": "w1", "fallback": "m1"})
    w1 = hub.devices["w1"]
    assert w1.name_by_user == "Guest Bath 1"  # carried over from the Matter copy
    await entries.remove_entry(rt, "mt", force=True)
    assert "m1" not in hub.devices and store.get_kv(duplicates.COMBINED_KEY) == []
    assert not hub.combined
    # The WiZ copy is itself again: shown, with its own name, as it was before combining.
    assert (w1.name_by_user, w1.area_id, w1.hidden_by) == (None, None, None)
    assert hub.states.get("light.wiz_tunable_white_000001").attributes["friendly_name"] == \
        "WiZ Tunable White 000001"


async def test_removing_an_unrelated_device_leaves_combinations_alone(env):
    hub, store, rt, *_ = env
    duplicates.combine(rt, {"main": "w1", "fallback": "m1"})
    await entries.remove_device(rt, "w2")
    assert len(store.get_kv(duplicates.COMBINED_KEY)) == 1 and hub.devices["m1"].hidden_by == "combined"


# -- the API -----------------------------------------------------------------------------------------

def test_the_routes(env):
    _hub, store, rt, matter, wiz = env
    c = TestClient(build_app(rt, Auth(store, SECRET)))
    assert c.post("/hub/devices/duplicates/combine", json={"main": "m1", "fallback": "w1"}).status_code == 401
    r = c.post("/hub/devices/duplicates/combine", json={"main": "m1", "fallback": "w1"}, headers=H)
    assert r.status_code == 200 and r.json()["combined"] == ["m1"]
    assert c.post("/hub/devices/duplicates/combine", json={"main": "m1", "fallback": "w1"},
                  headers=H).status_code == 409
    assert c.patch("/hub/devices/w1", json={"hidden": False}, headers=H).status_code == 409
    # The HA-compatible REST API reaches the fallback when the main fails.
    matter.dead.add("light.guest_bath_1_light")
    r = c.post("/api/services/light/turn_on", json={"entity_id": "light.guest_bath_1_light"}, headers=H)
    assert r.status_code == 200 and wiz.calls == [("light.wiz_tunable_white_000001", "turn_on")]
    r = c.post("/hub/devices/duplicates/swap", json={"device": "m1"}, headers=H)
    assert r.json()["combined"] == ["w1"]
    r = c.post("/hub/devices/duplicates/rule", json={"main_domain": "matter", "fallback_domain": "wiz"}, headers=H)
    assert r.json()["rules"][0]["main_domain"] == "matter"
    assert c.post("/hub/devices/duplicates/rule", json={"main_domain": "wiz"}, headers=H).status_code == 400
    r = c.post("/hub/devices/duplicates/uncombine", json={"device": "w1"}, headers=H)
    assert r.status_code == 200 and not any(g["combined"] for g in r.json()["groups"])
    assert c.post("/hub/devices/duplicates/uncombine", json={"device": "w1"}, headers=H).status_code == 404

