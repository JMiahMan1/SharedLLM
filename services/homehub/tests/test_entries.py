"""Managing set-up integrations: list, reconfigure, rename/move devices, remove and unpair, add by address.

Every id, address and secret here is made up.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import entries, pairing
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import Area, ConfigEntry, DeviceEntry, EntityEntry, Hub, State
from services.homehub.store import Store
from services.homehub.tests.test_flows import FakeRuntime

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture
def env(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7")
    hub.store = store
    for a in (Area("den", "Den"), Area("porch", "Porch")):
        hub.areas[a.area_id] = a
        store.save_area(a)
    rt = FakeRuntime(hub, store)

    def add(entry, devices, ents):
        hub.config_entries[entry.entry_id] = entry
        store.save_config_entry(entry)
        for d in devices:
            hub.devices[d.id] = d
            store.save_device(d)
        for e in ents:
            hub.entities[e.entity_id] = e
            store.save_entity(e)
            hub.states.set(e.entity_id, "on", {})
            store.save_last_state(State(e.entity_id, "on"))

    add(ConfigEntry("tv1", "samsungtv", "Den TV", {"host": "192.0.2.70", "method": "websocket", "token": "t0k"},
                    options={"host": "192.0.2.99"}),
        [DeviceEntry("d-tv", name="Den TV", area_id="den", config_entries=["tv1"])],
        [EntityEntry("media_player.den_tv", "samsungtv", "uid-1", "tv1", "d-tv"),
         EntityEntry("remote.den_tv", "samsungtv", "uid-1", "tv1", "d-tv")])
    add(ConfigEntry("hk1", "homekit_controller", "Bridge", {"AccessoryPairingID": "1A:2B", "iOSPairingId": "ctl"}),
        [DeviceEntry("d-br", name="Bridge", config_entries=["hk1"]),
         DeviceEntry("d-l1", name="Lamp", config_entries=["hk1"], via_device_id="d-br")],
        [EntityEntry("light.lamp", "homekit_controller", "1A:2B_2_10", "hk1", "d-l1"),
         EntityEntry("button.bridge_identify", "homekit_controller", "1A:2B_1_1_2", "hk1", "d-br")])
    add(ConfigEntry("rl1", "reolink", "Hub", {"host": "192.0.2.90", "username": "admin", "password": "pw"}),
        [DeviceEntry("d-rl", name="Hub", config_entries=["rl1"])], [])
    return hub, store, rt


def test_list_shows_devices_and_reconfigurable_fields_without_secrets(env):
    _hub, _store, rt = env
    out = entries.list_entries(rt)
    by_id = {e["entry_id"]: e for e in out["entries"]}
    tv = by_id["tv1"]
    assert tv["label"] == "Samsung TV" and tv["devices"][0]["entities"] == ["media_player.den_tv", "remote.den_tv"]
    # the address in use is the override, and the token is not reconfigurable
    assert tv["reconfigure"] == [{"key": "host", "label": "Address", "type": "text", "value": "192.0.2.99"}]
    rl = {f["key"]: f for f in by_id["rl1"]["reconfigure"]}
    assert rl["password"] == {"key": "password", "label": "Password", "type": "password", "set": True}
    assert "pw" not in str(out)
    assert by_id["hk1"]["unpairs"] is True
    assert [a["name"] for a in out["areas"]] == ["Den", "Porch"]


def test_an_entry_that_stopped_answering_says_since_when(env):
    hub, _store, rt = env
    by_id = lambda: {e["entry_id"]: e for e in entries.list_entries(rt)["entries"]}  # noqa: E731
    assert by_id()["tv1"]["offline_since"] is None  # loaded entries are not offline
    for eid in ("tv1", "rl1"):
        hub.config_entries[eid].state = "setup_retry"
    hub.states.set("media_player.den_tv", "unavailable", {})
    gone = hub.states.get("media_player.den_tv").last_changed
    # an entity that went unavailable says when; the same state set again keeps that time
    hub.states.set("media_player.den_tv", "unavailable", {})
    assert by_id()["tv1"]["offline_since"] == gone.isoformat()
    # an entry with no entities falls back to when this run of the hub saw it fail
    assert by_id()["rl1"]["offline_since"] is None
    rt.failing_since = {"rl1": gone}
    assert by_id()["rl1"]["offline_since"] == gone.isoformat()
    hub.config_entries["rl1"].disabled_by = "user"
    assert by_id()["rl1"]["offline_since"] is None  # switched off on purpose is not offline


async def test_reconfigure_changes_the_address_and_keeps_secrets_left_blank(env):
    hub, store, rt = env
    await entries.reconfigure_entry(rt, "rl1", {"data": {"host": "192.0.2.91", "password": ""}, "title": "Garage hub"})
    entry = hub.config_entries["rl1"]
    assert entry.data["host"] == "192.0.2.91" and entry.data["password"] == "pw" and entry.title == "Garage hub"
    assert rt.stopped == ["rl1"] and rt.started == ["rl1"]
    saved = {e.entry_id: e for e in store.load_config_entries()}["rl1"]
    assert saved.data["host"] == "192.0.2.91"
    await entries.reconfigure_entry(rt, "tv1", {"data": {"host": "192.0.2.71"}})
    assert hub.config_entries["tv1"].data["host"] == "192.0.2.71"
    assert "host" not in hub.config_entries["tv1"].options  # the old override would win otherwise
    with pytest.raises(entries.EntryError):
        await entries.reconfigure_entry(rt, "tv1", {"data": {"token": "steal"}})
    with pytest.raises(entries.EntryError):
        await entries.reconfigure_entry(rt, "tv1", {"data": {"host": ""}})


def test_rename_and_move_a_device(env):
    hub, store, rt = env
    entries.update_device(rt, "d-tv", {"name_by_user": "Living TV", "area_id": "Porch"})
    dev = hub.devices["d-tv"]
    assert dev.name_by_user == "Living TV" and dev.area_id == "porch"
    assert hub.states.get("media_player.den_tv").attributes["friendly_name"] == "Living TV"
    assert {d.id: d for d in store.load_devices()}["d-tv"].area_id == "porch"
    entries.update_device(rt, "d-tv", {"name_by_user": None, "area_id": None})
    assert hub.devices["d-tv"].name_by_user is None and hub.devices["d-tv"].area_id is None
    with pytest.raises(entries.EntryError):
        entries.update_device(rt, "d-tv", {"area_id": "Attic"})


async def test_removing_an_entry_unpairs_and_deletes_everything(env, monkeypatch):
    hub, store, rt = env
    unpaired = []

    async def unpair(runtime, entry):
        unpaired.append(entry.entry_id)

    monkeypatch.setitem(pairing.UNPAIR, "homekit_controller", unpair)
    result = await entries.remove_entry(rt, "hk1")
    assert unpaired == ["hk1"] and result["unpaired"] is True
    assert "hk1" not in hub.config_entries and "light.lamp" not in hub.entities
    assert hub.states.get("light.lamp") is None
    assert {"d-br", "d-l1"}.isdisjoint(hub.devices)
    assert "hk1" not in {e.entry_id for e in store.load_config_entries()}
    assert "light.lamp" not in {e.entity_id for e in store.load_entities()}
    assert "light.lamp" not in {s.entity_id for s in store.load_last_states()}
    assert {"d-br", "d-l1"}.isdisjoint({d.id for d in store.load_devices()})


async def test_an_unreachable_accessory_is_not_removed_unless_forced(env, monkeypatch):
    hub, _store, rt = env

    async def unpair(runtime, entry):
        raise OSError("no route to host")

    monkeypatch.setitem(pairing.UNPAIR, "homekit_controller", unpair)
    with pytest.raises(entries.EntryError) as err:
        await entries.remove_entry(rt, "hk1")
    assert err.value.status == 502 and "hk1" in hub.config_entries
    result = await entries.remove_entry(rt, "hk1", force=True)
    assert result["unpaired"] is False and "warning" in result and "hk1" not in hub.config_entries


async def test_removing_a_bridged_device_keeps_the_bridge(env):
    hub, _store, rt = env
    result = await entries.remove_device(rt, "d-l1")
    assert result["entities"] == ["light.lamp"]
    assert "hk1" in hub.config_entries and "d-br" in hub.devices and "d-l1" not in hub.devices


async def test_a_removed_entitys_registry_id_is_gone_after_a_restart(env):
    hub, store, rt = env
    hub.entity_registry_ids = {"uuid-lamp": "light.lamp", "uuid-other": "light.kept"}
    store.set_kv("entity_registry_ids", dict(hub.entity_registry_ids))
    await entries.remove_device(rt, "d-l1")
    assert store.get_kv("entity_registry_ids") == {"uuid-other": "light.kept"}


async def test_removing_the_only_device_removes_its_entry(env):
    hub, _store, rt = env
    result = await entries.remove_device(rt, "d-tv")
    assert result["status"] == "removed" and result["entry_id"] == "tv1"
    assert "tv1" not in hub.config_entries and "media_player.den_tv" not in hub.entities


async def test_removing_a_matter_device_decommissions_the_node(env):
    hub, _store, rt = env
    hub.config_entries["m1"] = ConfigEntry("m1", "matter", "Matter")
    hub.devices["d-m"] = DeviceEntry("d-m", name="Bulb", config_entries=["m1"],
                                     identifiers=[["matter", "deviceid_00000000000000AA-0000000000000050-MatterNodeDevice"]])
    sent = []

    class FakeMatter:
        def __init__(self):
            self.nodes = {80: {}}

        async def _send(self, command, **args):
            sent.append((command, args))

        def reindex(self, node_id=None):
            pass

    hub.drivers["m1"] = FakeMatter()
    await entries.remove_device(rt, "d-m")
    assert sent == [("remove_node", {"node_id": 80})]
    assert "m1" in hub.config_entries and "d-m" not in hub.devices


def test_the_api_routes(env, monkeypatch):
    hub, store, rt = env
    c = TestClient(build_app(rt, Auth(store, SECRET)))
    assert c.get("/hub/entries").status_code == 401
    assert c.get("/hub/entries", headers=H).json()["entries"]
    r = c.patch("/hub/devices/d-tv", headers=H, json={"name_by_user": "TV"})
    assert r.status_code == 200 and r.json()["name"] == "TV"
    assert c.patch("/hub/devices/nope", headers=H, json={}).status_code == 404
    r = c.patch("/hub/entries/tv1", headers=H, json={"data": {"token": "x"}})
    assert r.status_code == 400 and "cannot change token" in r.json()["message"]
    body = c.get("/hub/esphome-builder", headers=H).json()
    assert body["configured"] is False and "mounted" in body["configs"]
    flows_list = c.get("/hub/flows", headers=H).json()["flows"]
    assert {"samsungtv", "reolink", "dlna_dmr", "esphome_builder"} <= {f["domain"] for f in flows_list}

    async def check(url, username, password):
        return {"status": 200, "login": False}

    monkeypatch.setattr(pairing, "builder_check", check)
    r = c.post("/hub/flows/esphome_builder", headers=H, json={"url": "http://192.0.2.5:6052/"})
    assert r.status_code == 200 and r.json()["status"] == "adopted"
    info = c.get("/hub/esphome-builder", headers=H).json()
    assert info["configured"] is True and info["url"] == "http://192.0.2.5:6052/" and "password" not in info
    assert c.post("/hub/flows/zigbee", headers=H, json={"host": "192.0.2.1"}).status_code == 501
    r = c.delete("/hub/entries/rl1", headers=H)
    assert r.status_code == 200 and "rl1" not in hub.config_entries


def test_a_pending_pairing_answers_with_its_session(env, monkeypatch):
    _hub, store, rt = env
    import asyncio

    async def probe(host):
        return {"method": "websocket", "port": 8002, "info": {"device": {"id": "uuid:abc"}}}

    async def ws_pair(host, port):
        await asyncio.sleep(3600)

    monkeypatch.setattr(pairing, "samsung_probe", probe)
    monkeypatch.setattr(pairing, "samsung_ws_pair", ws_pair)
    c = TestClient(build_app(rt, Auth(store, SECRET)))
    r = c.post("/hub/flows/samsungtv", headers=H, json={"host": "192.0.2.75"})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "pending" and body["step"] == "accept_on_tv"
    assert body["session"] and body["poll_ms"] > 0
    pairing.drop_session(body["session"])
