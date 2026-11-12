"""The hardware list (integrations, devices, entities), disabling, entity sync, and moved devices.

Every id, address and MAC here is made up (RFC 5737 addresses, 02:00:00 MACs).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import discovery, hardware
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import UNAVAILABLE, Area, ConfigEntry, DeviceEntry, EntityEntry, Hub
from services.homehub.discovery import DiscoveryManager
from services.homehub.entries import EntryError
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
    rt = FakeRuntime(hub, store)
    sent: list[dict] = []

    async def notifier(payload):
        sent.append(payload)
        return True

    rt.discovery = DiscoveryManager(hub, store, notifier=notifier)

    def add(entry, devices=(), ents=(), state="loaded"):
        entry.state = state
        hub.config_entries[entry.entry_id] = entry
        store.save_config_entry(entry)
        for d in devices:
            hub.devices[d.id] = d
            store.save_device(d)
        for e in ents:
            hub.entities[e.entity_id] = e
            store.save_entity(e)

    add(ConfigEntry("wiz1", "wiz", "Porch light", {"host": "192.0.2.21"}, unique_id="020000000021"),
        [DeviceEntry("d-wiz", name="Porch light", area_id="porch", manufacturer="WiZ", model="ESP25_SHRGB_01",
                     sw_version="1.36.1", config_entries=["wiz1"], connections=[["mac", "02:00:00:00:00:21"]])],
        [EntityEntry("light.porch_light", "wiz", "020000000021", "wiz1", "d-wiz"),
         EntityEntry("sensor.porch_light_power", "wiz", "020000000021_power", "wiz1", "d-wiz",
                     entity_category="diagnostic")])
    add(ConfigEntry("rk1", "roku", "Den TV", {"host": "192.0.2.66"}, unique_id="X00000000001"),
        [DeviceEntry("d-rk", name="Den TV", area_id="den", manufacturer="Roku", config_entries=["rk1"],
                     identifiers=[["roku", "X00000000001"]])],
        [EntityEntry("media_player.den_tv", "roku", "X00000000001", "rk1", "d-rk"),
         EntityEntry("remote.den_tv", "roku", "X00000000001", "rk1", "d-rk")])
    add(ConfigEntry("ma1", "music_assistant", "Music server", {"url": "http://192.0.2.5:8095"}),
        state="setup_retry")
    hub.config_entries["ma1"].reason = "DeviceUnavailable: could not connect"
    hub.states.set("light.porch_light", "on", {"friendly_name": "Porch light"})
    hub.states.set("sensor.porch_light_power", "4.2", {"unit_of_measurement": "W"})
    hub.states.set("media_player.den_tv", "idle", {})
    hub.states.set("remote.den_tv", UNAVAILABLE, {})
    return hub, store, rt, sent


# -- the device list --------------------------------------------------------------------

def test_device_list_is_built_from_the_registry_with_connections_and_status(env):
    _hub, _store, rt, _sent = env
    out = hardware.device_list(rt)
    by_id = {d["id"]: d for d in out["devices"]}
    wiz = by_id["d-wiz"]
    assert wiz["manufacturer"] == "WiZ" and wiz["sw_version"] == "1.36.1"
    assert wiz["area"] == "Porch" and wiz["label"] == "WiZ" and wiz["entry_title"] == "Porch light"
    assert {"type": "mac", "value": "02:00:00:00:00:21"} in wiz["connections"]
    assert {"type": "ip", "value": "192.0.2.21"} in wiz["connections"]
    assert wiz["status"] == "online" and wiz["entities"] == 2 and wiz["last_seen"]
    tv = by_id["d-rk"]
    assert tv["status"] == "partial" and tv["unavailable"] == 1
    assert [a["name"] for a in out["areas"]] == ["Den", "Porch"]


def test_detail_lists_entities_with_states_main_first(env):
    _hub, _store, rt, _sent = env
    out = hardware.device_detail(rt, "d-wiz")
    ids = [e["entity_id"] for e in out["entity_list"]]
    assert ids == ["light.porch_light", "sensor.porch_light_power"]  # diagnostics after the main entity
    light = out["entity_list"][0]
    assert light["state"]["state"] == "on" and light["state"]["domain"] == "light"
    assert out["entry"]["entry_id"] == "wiz1"
    with pytest.raises(EntryError) as err:
        hardware.device_detail(rt, "nope")
    assert err.value.status == 404


def test_an_ip_comes_from_discovery_when_the_entry_has_none(env):
    hub, _store, rt, _sent = env
    hub.devices["d-rk"].connections = [["mac", "02:00:00:00:00:66"]]
    rt.discovery.record({"id": "roku:x", "integration": "roku", "host": "192.0.2.67", "mac": "020000000066",
                         "ids": ["020000000066"], "props": {}})
    view = hardware.device_view(rt, hub.devices["d-rk"])
    assert view["connections"][0] == {"type": "ip", "value": "192.0.2.67"}


# -- integrations -----------------------------------------------------------------------

def test_integrations_have_counts_status_and_how_to_add(env):
    _hub, _store, rt, _sent = env
    rows = {r["domain"]: r for r in hardware.integrations(rt)["integrations"]}
    assert rows["wiz"]["status"] == "loaded" and rows["wiz"]["devices"] == 1 and rows["wiz"]["entities"] == 2
    assert rows["roku"]["unavailable"] == 1
    ma = rows["music_assistant"]
    assert ma["status"] == "setup_retry" and ma["entries"][0]["reason"].startswith("DeviceUnavailable")
    assert ma["syncs"] is True and ma["discovery"] == ["zeroconf"]
    # every driver is listed even when nothing is set up, so it can be added from here
    assert rows["sun"]["status"] == "not_set_up" and rows["sun"]["manual"] is True
    assert rows["mobile_app"]["manual"] is False and "app" in rows["mobile_app"]["self_added"]


def test_an_entry_with_no_driver_is_unsupported_not_an_error(env):
    hub, _store, rt, _sent = env
    hub.config_entries["x1"] = ConfigEntry("x1", "example_cloud", "Example", state="not_loaded",
                                           reason="no driver for this integration yet")
    row = next(r for r in hardware.integrations(rt)["integrations"] if r["domain"] == "example_cloud")
    assert row["status"] == "unsupported" and row["has_driver"] is False


def test_every_driver_can_be_added_by_hand_or_adds_itself():
    from services.homehub import manual_flows
    from services.homehub.drivers import DRIVERS

    for domain in DRIVERS:
        assert domain in manual_flows.all_manual() or domain in hardware.SELF_ADDED, domain


# -- entities, devices, entries -------------------------------------------------------------

async def test_renaming_an_entity_updates_its_friendly_name(env):
    hub, store, rt, _sent = env
    out = await hardware.update_entity(rt, "light.porch_light", {"name": "Front lamp", "area_id": "Den"})
    assert out["name"] == "Front lamp" and hub.entities["light.porch_light"].area_id == "den"
    assert hub.states.get("light.porch_light").attributes["friendly_name"] == "Front lamp"
    assert rt.stopped == []  # a rename needs no reload
    saved = {e.entity_id: e for e in store.load_entities()}["light.porch_light"]
    assert saved.name == "Front lamp"


async def test_disabling_an_entity_drops_its_state_and_reloads_the_entry(env):
    hub, _store, rt, _sent = env
    await hardware.update_entity(rt, "sensor.porch_light_power", {"disabled": True})
    assert hub.entities["sensor.porch_light_power"].disabled_by == "user"
    assert hub.states.get("sensor.porch_light_power") is None
    assert rt.stopped == ["wiz1"] and rt.started == ["wiz1"]
    await hardware.update_entity(rt, "sensor.porch_light_power", {"disabled": False})
    assert hub.entities["sensor.porch_light_power"].disabled_by is None


async def test_entity_changes_are_checked(env):
    _hub, _store, rt, _sent = env
    with pytest.raises(EntryError):
        await hardware.update_entity(rt, "light.porch_light", {"entity_id": "light.other"})
    with pytest.raises(EntryError):
        await hardware.update_entity(rt, "light.porch_light", {"area_id": "Attic"})
    with pytest.raises(EntryError) as err:
        await hardware.update_entity(rt, "light.nope", {"name": "x"})
    assert err.value.status == 404


async def test_disabling_a_device_disables_its_entities_and_enabling_restores_only_those(env):
    hub, _store, rt, _sent = env
    hub.entities["sensor.porch_light_power"].disabled_by = "user"
    out = await hardware.set_device_disabled(rt, "d-wiz", True)
    assert out["status"] == "disabled" and out["changed_entities"] == ["light.porch_light"]
    assert hub.entities["light.porch_light"].disabled_by == "device"
    await hardware.set_device_disabled(rt, "d-wiz", False)
    assert hub.entities["light.porch_light"].disabled_by is None
    assert hub.entities["sensor.porch_light_power"].disabled_by == "user"  # the user's own choice stays


async def test_disabling_an_entry_unloads_it_and_marks_its_entities_unavailable(env):
    hub, store, rt, _sent = env
    out = await hardware.set_entry_disabled(rt, "rk1", True)
    assert out["disabled_by"] == "user" and rt.stopped == ["rk1"] and rt.started == []
    assert hub.states.get("media_player.den_tv").state == UNAVAILABLE
    assert {e.entry_id: e for e in store.load_config_entries()}["rk1"].disabled_by == "user"
    await hardware.set_entry_disabled(rt, "rk1", False)
    assert hub.config_entries["rk1"].disabled_by is None and rt.started == ["rk1"]


# -- entity sync ------------------------------------------------------------------------------

async def test_sync_adds_music_assistant_players_the_driver_found(env):
    hub, _store, rt, _sent = env
    entry = hub.config_entries["ma1"]
    with pytest.raises(EntryError) as err:
        await hardware.sync_entities(rt, "ma1")
    assert err.value.status == 409 and "not connected" in str(err.value)
    entry.state, entry.reason = "loaded", None
    hub.drivers["ma1"] = SimpleNamespace(players={
        "up000000000001": {"player_id": "up000000000001", "display_name": "Kitchen speaker",
                           "device_info": {"manufacturer": "Example", "model": "Speaker"}}})
    out = await hardware.sync_entities(rt, "ma1")
    assert out["entities"] == ["media_player.kitchen_speaker"]
    ent = hub.entities["media_player.kitchen_speaker"]
    assert ent.unique_id == "up000000000001" and hub.devices[ent.device_id].model == "Speaker"
    again = await hardware.sync_entities(rt, "ma1")
    assert again["entities"] == []  # nothing twice


async def test_sync_adds_emporia_circuits_and_roborock_vacuums(env):
    hub, _store, rt, _sent = env
    hub.config_entries["ev1"] = ConfigEntry("ev1", "emporia_vue", "Energy", state="loaded")
    channel = SimpleNamespace(channel_num="1", name="Dryer")
    hub.drivers["ev1"] = SimpleNamespace(devices={100001: SimpleNamespace(
        device_gid=100001, device_name="Panel", model="VUE002", firmware="x", channels=[channel])})
    out = await hardware.sync_entities(rt, "ev1")
    uids = sorted(hub.entities[e].unique_id for e in out["entities"])
    assert uids == ["sensor.emporia_vue.1D.100001-1", "sensor.emporia_vue.1MON.100001-1",
                    "sensor.emporia_vue.instant.100001-1"]
    hub.config_entries["rr1"] = ConfigEntry("rr1", "roborock", "Vacuums", state="loaded")
    hub.drivers["rr1"] = SimpleNamespace(devices={"abcdef": SimpleNamespace(
        duid="ABCDEF", name="Downstairs", product=SimpleNamespace(model="roborock.vacuum.a1", name="S"))})
    out = await hardware.sync_entities(rt, "rr1")
    assert [hub.entities[e].unique_id for e in out["entities"]] == ["abcdef"]


async def test_sync_is_refused_for_integrations_that_add_devices_in_their_flow(env):
    _hub, _store, rt, _sent = env
    with pytest.raises(EntryError) as err:
        await hardware.sync_entities(rt, "wiz1")
    assert err.value.status == 400


# -- discovery: Thread, Music Assistant, DHCP/ARP, moved devices ------------------------------

def test_thread_homekit_is_found_but_said_to_be_unsupported(env):
    _hub, _store, rt, _sent = env
    f = discovery.parse_mdns("_hap._udp.local.", "Sensor._hap._udp.local.", "192.0.2.80", 5683,
                             {b"sf": b"1", b"id": b"AA:BB:CC:00:00:01", b"md": b"Sensor", b"ci": b"10"})
    assert f["integration"] == "homekit_controller" and f["props"]["transport"] == "thread"
    rt.discovery.record(f)
    pub = rt.discovery.public(f)
    assert pub["adoptable"] is False and "Thread" in pub["unsupported"]


def test_music_assistant_servers_are_found_by_mdns():
    f = discovery.parse_mdns("_mass._tcp.local.", "abc._mass._tcp.local.", "192.0.2.5", 8095,
                             {b"server_id": b"0123abcd", b"base_url": b"http://192.0.2.5:8095",
                              b"server_version": b"2.6.0"})
    assert f["id"] == "music_assistant:0123abcd" and f["props"]["url"] == "http://192.0.2.5:8095"


ARP = """IP address       HW type     Flags       HW address            Mask     Device
192.0.2.30       0x1         0x2         ec:71:db:00:00:30     *        eth0
192.0.2.31       0x1         0x2         02:00:00:00:00:31     *        eth0
192.0.2.32       0x1         0x0         00:00:00:00:00:00     *        eth0
192.0.2.66       0x1         0x2         02:00:00:00:00:66     *        eth0
"""


def test_arp_and_dhcp_rules():
    assert discovery.parse_arp(ARP) == {"192.0.2.30": "ec71db000030", "192.0.2.31": "020000000031",
                                        "192.0.2.66": "020000000066"}
    assert discovery.dhcp_match("ec:71:db:00:00:30", None) == "reolink"
    assert discovery.dhcp_match("02:00:00:00:00:31", "roborock-vacuum-a1.lan") == "roborock"
    assert discovery.dhcp_match("02:00:00:00:00:31", "laptop") is None
    assert discovery.parse_dhcp("192.0.2.40", "a8bb50000040", None) is None  # WiZ is found by its own protocol


async def test_a_dhcp_scan_offers_vendor_devices_and_fills_in_macs(env, monkeypatch):
    _hub, _store, rt, _sent = env
    mgr = rt.discovery
    mgr.record({"id": "roku:x00000000001", "integration": "roku", "host": "192.0.2.66", "mac": None,
                "ids": ["X00000000001"], "props": {}})
    monkeypatch.setattr(mgr, "read_arp", lambda: ARP)

    async def names(host):
        return {"192.0.2.31": "roborock-vacuum-a1.lan"}.get(host)

    monkeypatch.setattr(mgr, "reverse_name", names)
    found = await mgr._scan_dhcp()
    assert found == 2
    assert mgr.findings["reolink:ec71db000030"]["source"] == "dhcp"
    assert mgr.findings["roborock:020000000031"]["name"] == "roborock-vacuum-a1"
    assert mgr.findings["roku:x00000000001"]["mac"] == "020000000066"


async def test_an_unreadable_neighbour_table_is_reported_in_the_scan(env, monkeypatch):
    _hub, _store, rt, _sent = env
    mgr = rt.discovery

    async def nothing(_hosts):
        return []

    def broken():
        raise RuntimeError("the neighbour table /x cannot be read: denied")

    monkeypatch.setattr(mgr, "ssdp_search", nothing)
    monkeypatch.setattr(mgr, "wiz_sweep", nothing)
    monkeypatch.setattr(mgr, "subnet_hosts", lambda: [])
    monkeypatch.setattr(mgr, "read_arp", broken)
    out = await mgr.scan()
    assert out["found"]["dhcp"].startswith("failed: the neighbour table")


def test_a_roborock_vacuum_is_known_once_its_account_is_set_up(env):
    hub, _store, rt, _sent = env
    f = discovery.parse_dhcp("192.0.2.31", "020000000031", "roborock-vacuum-a1")
    rt.discovery.record(f)
    assert rt.discovery.public(f)["new"] is True
    hub.config_entries["rr1"] = ConfigEntry("rr1", "roborock", "Vacuums")
    pub = rt.discovery.public(f)
    assert pub["known"] is True and pub["moved"] is None


async def test_a_device_at_a_new_address_is_offered_as_an_update_and_updated(env):
    hub, _store, rt, sent = env
    mgr = rt.discovery
    f = mgr.record({"id": "roku:x00000000001", "integration": "roku", "host": "192.0.2.96", "mac": None,
                    "ids": ["X00000000001"], "props": {}})
    pub = mgr.public(f)
    assert pub["known"] is True and pub["new"] is False
    assert pub["moved"] == {"entry_id": "rk1", "entry_title": "Den TV", "old_host": "192.0.2.66",
                            "new_host": "192.0.2.96"}
    await mgr.flush_notices()
    await mgr.flush_notices()
    moved_notices = [p for p in sent if "new address" in str(p)]
    assert len(moved_notices) == 1  # told once per new address
    out = await hardware.update_address(rt, "roku:x00000000001")
    assert out["new_host"] == "192.0.2.96" and hub.config_entries["rk1"].data["host"] == "192.0.2.96"
    assert rt.stopped == ["rk1"] and rt.started == ["rk1"]
    assert mgr.public(f)["moved"] is None
    with pytest.raises(EntryError) as err:
        await hardware.update_address(rt, "roku:x00000000001")
    assert err.value.status == 409


def test_a_host_name_or_a_match_by_address_is_never_moved(env):
    hub, _store, rt, _sent = env
    hub.config_entries["rk1"].data["host"] = "den-tv.local"
    f = rt.discovery.record({"id": "roku:x00000000001", "integration": "roku", "host": "192.0.2.96",
                             "ids": ["X00000000001"], "props": {}})
    assert rt.discovery.public(f)["moved"] is None
    g = rt.discovery.record({"id": "wiz:y", "integration": "wiz", "host": "192.0.2.21", "ids": [], "props": {}})
    assert rt.discovery.public(g)["moved"] is None


async def test_a_moved_device_with_a_url_gets_the_new_host_in_its_url(env):
    hub, _store, rt, _sent = env
    hub.config_entries["dl1"] = ConfigEntry("dl1", "dlna_dmr", "Renderer",
                                            {"url": "http://192.0.2.70:9197/dmr", "device_id": "uuid:0a0b0c0d0e0f"},
                                            unique_id="0a0b0c0d0e0f")
    rt.discovery.record({"id": "dlna_dmr:0a0b0c0d0e0f", "integration": "dlna_dmr", "host": "192.0.2.71",
                         "ids": ["0a0b0c0d0e0f"], "props": {}})
    await hardware.update_address(rt, "dlna_dmr:0a0b0c0d0e0f")
    assert hub.config_entries["dl1"].data["url"] == "http://192.0.2.71:9197/dmr"


# -- the API -----------------------------------------------------------------------------------

def test_the_api_is_admin_only_and_answers(env):
    hub, _store, rt, _sent = env
    app = build_app(rt, Auth(hub, SECRET))
    client = TestClient(app)
    assert client.get("/hub/hardware").status_code == 401
    assert client.get("/hub/integrations").status_code == 401
    devices = client.get("/hub/hardware", headers=H).json()["devices"]
    assert {d["id"] for d in devices} == {"d-wiz", "d-rk"}
    assert client.get("/hub/hardware/d-wiz", headers=H).json()["entity_list"][0]["entity_id"] == "light.porch_light"
    assert client.get("/hub/hardware/none", headers=H).status_code == 404
    r = client.patch("/hub/entities/light.porch_light", headers=H, json={"name": "Lamp"})
    assert r.status_code == 200 and r.json()["name"] == "Lamp"
    assert client.post("/hub/entries/rk1/disable", headers=H, json={"disabled": "yes"}).status_code == 400
    assert client.post("/hub/entries/rk1/disable", headers=H, json={"disabled": True}).json()["disabled_by"] == "user"
    assert client.post("/hub/entries/rk1/reload", headers=H).status_code == 409  # disabled: enable first
    assert client.post("/hub/hardware/d-wiz/disable", headers=H, json={"disabled": True}).json()["status"] == "disabled"
    assert client.post("/hub/entries/wiz1/sync", headers=H).status_code == 400
    assert client.post("/hub/discovered/none/update-address", headers=H).status_code == 404
    rows = client.get("/hub/integrations", headers=H).json()["integrations"]
    assert any(r["domain"] == "roku" and r["status"] == "disabled" for r in rows)
