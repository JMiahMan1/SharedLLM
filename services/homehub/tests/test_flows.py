"""Adopt flows: each creates the records its driver expects, and the driver accepts them.

Every address, MAC, serial and key here is made up.
"""
from __future__ import annotations

import pytest
from aioesphomeapi import (
    DeviceInfo,
    EntityCategory,
    InvalidEncryptionKeyAPIError,
    LightInfo,
    SensorInfo,
    TextSensorInfo,
)
from cryptography.fernet import Fernet

from services.homehub import flows
from services.homehub.core import Area, ConfigEntry, DeviceUnavailable, EntityEntry, Hub
from services.homehub.discovery import DiscoveryManager, parse_mdns, parse_wiz
from services.homehub.runtime import Runtime
from services.homehub.store import Store

MAC = "aabbccddee01"
PSK = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="  # made up, base64 of 32 bytes
FABRIC = 0x1122334455667788


class FakeRuntime(Runtime):
    def __init__(self, hub, store):
        super().__init__(hub, store)
        self.started: list[str] = []
        self.stopped: list[str] = []

    def start_entry(self, entry):
        self.started.append(entry.entry_id)

    async def stop_entry(self, entry_id):
        self.stopped.append(entry_id)


@pytest.fixture
def rt(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="America/Phoenix")
    hub.store = store
    hub.areas["porch"] = Area("porch", "Porch")
    return FakeRuntime(hub, store)


def esphome_finding(encrypted=True):
    props = {b"friendly_name": b"Porch Bulb", b"mac": MAC.encode(), b"platform": b"ESP8266"}
    if encrypted:
        props[b"api_encryption"] = b"Noise_NNpsk0_25519_ChaChaPoly_SHA256"
    return parse_mdns("_esphomelib._tcp.local.", "porch-bulb._esphomelib._tcp.local.", "192.0.2.34", 6053, props,
                      "porch-bulb.local.")


def esphome_device():
    info = DeviceInfo(name="porch-bulb", friendly_name="Porch Bulb", mac_address="AA:BB:CC:DD:EE:01",
                      esphome_version="2026.6.2", manufacturer="Espressif", model="esp8285",
                      project_name="Example.RGBWW", project_version="1.0", webserver_port=80)
    infos = [
        LightInfo(object_id="porch_bulb", key=1, name=""),
        SensorInfo(object_id="wifi_signal", key=2, name="WiFi Signal", unit_of_measurement="dBm",
                   device_class="signal_strength", entity_category=EntityCategory.DIAGNOSTIC),
        TextSensorInfo(object_id="ip", key=3, name="IP Address", entity_category=EntityCategory.DIAGNOSTIC,
                       disabled_by_default=True),
    ]
    return info, infos


# -- ESPHome ----------------------------------------------------------------------------

async def test_esphome_wrong_key_asks_again_and_right_key_adds_the_node(rt, monkeypatch):
    calls = []

    async def probe(host, port, psk, password=""):
        calls.append(psk)
        if psk != PSK:
            raise InvalidEncryptionKeyAPIError("bad key")
        return esphome_device()

    monkeypatch.setattr(flows, "esphome_probe", probe)
    finding = esphome_finding()
    with pytest.raises(flows.NeedsInput) as need:
        await flows.adopt(rt, finding, {"input": {"noise_psk": "d3Jvbmc="}})
    assert need.value.error == "That key was not accepted."

    result = await flows.adopt(rt, finding, {"name": "Front porch", "area_id": "Porch", "input": {"noise_psk": PSK}})
    hub = rt.hub
    entry = hub.config_entries[result["entry_id"]]
    assert entry.domain == "esphome" and entry.unique_id == "aa:bb:cc:dd:ee:01"
    assert entry.data == {"host": "192.0.2.34", "port": 6053, "password": "", "noise_psk": PSK,
                          "device_name": "porch-bulb"}
    dev = hub.devices[result["device_id"]]
    assert dev.name_by_user == "Front porch" and dev.area_id == "porch"
    assert dev.connections == [["mac", "aa:bb:cc:dd:ee:01"]]
    uids = {hub.entities[e].unique_id: e for e in result["entities"]}
    assert uids == {
        "AA:BB:CC:DD:EE:01/0/light/": "light.front_porch",
        "AA:BB:CC:DD:EE:01/0/sensor/WiFi Signal": "sensor.front_porch_wifi_signal",
        "AA:BB:CC:DD:EE:01/0/text_sensor/IP Address": "sensor.front_porch_ip_address",
    }
    assert hub.entities["sensor.front_porch_ip_address"].disabled_by == "integration"
    assert rt.started == [entry.entry_id]
    # saved: a restart finds the same records, with the key sealed
    assert any(e.entry_id == entry.entry_id and e.data["noise_psk"] == PSK for e in rt.store.load_config_entries())

    # the ESPHome driver matches every created entity to the node's entity
    from services.homehub.drivers.esphome import ESPHomeDriver

    drv = ESPHomeDriver(hub, entry)
    drv._index(esphome_device()[1])
    assert set(drv._info_for) == {"light.front_porch", "sensor.front_porch_wifi_signal"}  # the disabled one is skipped

    with pytest.raises(flows.FlowError) as dup:
        await flows.adopt(rt, finding, {"input": {"noise_psk": PSK}})
    assert dup.value.status == 409


async def test_a_plain_esphome_node_needs_no_input(rt, monkeypatch):
    async def probe(host, port, psk, password=""):
        assert psk is None
        return esphome_device()

    monkeypatch.setattr(flows, "esphome_probe", probe)
    assert flows.needs_for(esphome_finding(encrypted=False)) == []
    result = await flows.adopt(rt, esphome_finding(encrypted=False), {})
    assert rt.hub.config_entries[result["entry_id"]].data["noise_psk"] == ""


# -- WiZ, Cast, Roku, Tasmota ------------------------------------------------------------

async def test_wiz_adds_a_light_speed_and_power_sensor_the_driver_finds(rt, monkeypatch):
    async def wiz(host, method):
        assert host == "192.0.2.42"
        if method == "getSystemConfig":
            return {"mac": "aabbcc0a1b2c", "moduleName": "ESP25_SHRGB_01", "fwVersion": "1.30.40"}
        if method == "getPower":
            return {"power": 5000}
        raise AssertionError(method)

    monkeypatch.setattr(flows, "wiz_request", wiz)
    finding = parse_wiz({"result": {"mac": "aabbcc0a1b2c", "moduleName": "ESP25_SHRGB_01"}}, "192.0.2.42")
    result = await flows.adopt(rt, finding, {})
    hub = rt.hub
    entry = hub.config_entries[result["entry_id"]]
    assert entry.data == {"host": "192.0.2.42"} and entry.unique_id == "aabbcc0a1b2c"
    assert entry.title == "WiZ RGB Tunable 0A1B2C"
    assert sorted(result["entities"]) == ["light.wiz_rgb_tunable_0a1b2c", "number.wiz_rgb_tunable_0a1b2c_effect_speed",
                                          "sensor.wiz_rgb_tunable_0a1b2c_power"]
    from services.homehub.drivers.wiz import WizDriver

    drv = WizDriver(hub, entry)
    assert drv.light.entity_id == "light.wiz_rgb_tunable_0a1b2c"
    assert drv.speed is not None and drv.power is not None


async def test_wiz_that_does_not_answer_is_a_clear_error(rt, monkeypatch):
    async def silent(host, method):
        raise DeviceUnavailable("no answer")

    monkeypatch.setattr(flows, "wiz_request", silent)
    finding = parse_wiz({"result": {"mac": "aabbcc0a1b2c", "moduleName": "ESP21_SHTW_01"}}, "192.0.2.42")
    with pytest.raises(flows.FlowError) as err:
        await flows.adopt(rt, finding, {})
    assert err.value.status == 502


async def test_cast_joins_the_existing_cast_entry(rt):
    hub = rt.hub
    entry = ConfigEntry("cast1", "cast", "Google Cast", data={"known_hosts": ["192.0.2.80"]})
    hub.config_entries["cast1"] = entry
    finding = parse_mdns("_googlecast._tcp.local.", "x._googlecast._tcp.local.", "192.0.2.88", 8009,
                         {b"id": b"11112222333344445555666677778888", b"fn": b"Den TV", b"md": b"Chromecast"},
                         "c.local.")
    result = await flows.adopt(rt, finding, {})
    assert result["entry_id"] == "cast1"
    assert entry.data["known_hosts"] == ["192.0.2.80", "192.0.2.88"]
    ent = hub.entities["media_player.den_tv"]
    assert ent.unique_id == "11112222-3333-4444-5555-666677778888"
    assert rt.stopped == ["cast1"] and rt.started == ["cast1"]
    from services.homehub.drivers.cast import CastDriver

    assert "11112222-3333-4444-5555-666677778888" in CastDriver(hub, entry).wanted()


async def test_roku_adds_the_media_player_remote_and_diagnostics(rt, monkeypatch):
    async def info(host):
        return {"serial-number": "X00000000001", "user-device-name": "Den Roku", "model-name": "Roku Ultra",
                "vendor-name": "Roku", "software-version": "15.3.4", "is-tv": "false", "wifi-mac": "aa:bb:cc:dd:ee:05"}

    monkeypatch.setattr(flows, "roku_device_info", info)
    finding = {"id": "roku:x00000000001", "integration": "roku", "host": "192.0.2.96", "ids": ["X00000000001"],
               "props": {"serial": "X00000000001"}}
    result = await flows.adopt(rt, finding, {})
    hub = rt.hub
    entry = hub.config_entries[result["entry_id"]]
    assert entry.unique_id == "X00000000001" and entry.data == {"host": "192.0.2.96"}
    assert hub.entities["media_player.den_roku"].unique_id == "X00000000001"
    assert hub.entities["remote.den_roku"].unique_id == "X00000000001"
    assert hub.entities["binary_sensor.den_roku_supports_airplay"].unique_id == "X00000000001_supports_airplay"
    assert hub.devices[result["device_id"]].identifiers == [["roku", "X00000000001"]]


async def test_tasmota_asks_for_a_password_then_adds_relays_and_energy(rt, monkeypatch):
    async def status(host, password):
        if password != "pw":
            raise flows.NeedsInput([flows.TASMOTA_PASSWORD], "asks", None)
        return {"Status": {"DeviceName": "Loft Plug", "FriendlyName": ["Loft Plug"]},
                "StatusNET": {"Mac": "AA:BB:CC:DD:EE:07"}, "StatusFWR": {"Version": "14.1.0"},
                "StatusSTS": {"POWER": "ON"}, "StatusSNS": {"ENERGY": {"Power": 12, "Total": 1.5}}}

    monkeypatch.setattr(flows, "tasmota_status", status)
    finding = {"id": "tasmota:tasmota-x", "integration": "tasmota", "host": "192.0.2.90", "ids": [], "props": {}}
    with pytest.raises(flows.NeedsInput):
        await flows.adopt(rt, finding, {})
    result = await flows.adopt(rt, finding, {"input": {"password": "pw"}})
    hub = rt.hub
    entry = hub.config_entries[result["entry_id"]]
    assert entry.data == {"host": "192.0.2.90", "username": "admin", "password": "pw"}
    from services.homehub.drivers.tasmota import TasmotaDriver

    drv = TasmotaDriver(hub, entry)
    assert list(drv.relays.values()) == [0]
    assert sorted(drv.energy.values()) == ["Power", "Total"]
    assert drv.host == "192.0.2.90"


async def test_unsupported_integrations_are_refused_plainly(rt):
    for integration in ("zigbee", "nest"):
        with pytest.raises(flows.FlowError) as err:
            await flows.adopt(rt, {"id": f"{integration}:x", "integration": integration, "host": "192.0.2.9"}, {})
        assert err.value.status == 501
    with pytest.raises(flows.FlowError):
        await flows.adopt(rt, esphome_finding(), {"area_id": "Nowhere"})


# -- Matter -------------------------------------------------------------------------------

NEW_NODE = {
    "node_id": 80, "available": True,
    "attributes": {
        "0/40/1": "Example Co", "0/40/3": "Smart Bulb", "0/40/5": "", "0/40/9": 3, "0/40/10": "1.2.0",
        "0/40/15": "SN-0001", "0/42/0": [], "0/54/4": -50, "0/51/1": 2, "0/51/2": 3600,
        "1/29/0": [{"0": 269, "1": 1}], "1/3/0": 0,
        "1/6/0": True, "1/6/16387": None, "1/8/0": 200, "1/8/17": None,
        "1/768/65532": 25, "1/768/16395": 153, "1/768/16396": 370, "1/768/8": 2, "1/768/7": 300,
    },
}
PLUG_NODE = {"node_id": 81, "available": True,
             "attributes": {"0/40/3": "Smart Plug", "1/29/0": [{"0": 266, "1": 1}], "1/6/0": False}}


def test_matter_codes_are_checked():
    assert flows.normalize_matter_code("mt:y.k9042c00kcs.u20") == "MT:Y.K9042C00KCS.U20"
    assert flows.normalize_matter_code("3497-011-2332") == "34970112332"
    for bad in ("", "1234", "MT:short", "abcdefghijk"):
        with pytest.raises(flows.FlowError):
            flows.normalize_matter_code(bad)


async def test_matter_commission_registers_the_node_and_the_driver_serves_it(rt):
    from services.homehub.drivers.matter import MatterDriver

    hub = rt.hub
    entry = ConfigEntry("m1", "matter", "Matter", data={"url": "ws://localhost:5580/ws"})
    hub.config_entries["m1"] = entry
    drv = MatterDriver(hub, entry)
    drv.server_info = {"compressed_fabric_id": FABRIC}
    sent = []

    async def send(command, *, timeout=30.0, **args):
        sent.append((command, args))
        return dict(NEW_NODE) if command == "commission_with_code" else None

    drv._send = send  # type: ignore[method-assign]
    hub.drivers["m1"] = drv

    with pytest.raises(flows.FlowError):
        await flows.commission_matter(rt, "12")
    result = await flows.commission_matter(rt, "3497-011-2332", name="Desk lamp")
    assert sent[0] == ("commission_with_code", {"code": "34970112332", "network_only": True})
    assert result["node_id"] == 80 and result["entry_id"] == "m1"
    prefix = f"{FABRIC:016X}-{80:016X}-MatterNodeDevice"
    uids = {hub.entities[e].unique_id for e in result["entities"]}
    assert f"{prefix}-1-MatterLight-6-0" in uids
    assert f"{prefix}-1-MatterStartUpOnOff-6-16387" in uids
    assert f"{prefix}-1-IdentifyButton-3-65529" in uids
    assert f"{prefix}-1-on_level-8-17" in uids
    assert f"{prefix}-0-WiFiDiagnosticsRssi-54-4" in uids
    assert f"{prefix}-0-MatterUpdate-40-9" in uids
    dev = hub.devices[result["device_id"]]
    assert dev.name == "Smart Bulb" and dev.name_by_user == "Desk lamp" and dev.serial_number == "SN-0001"
    assert ["matter", f"deviceid_{prefix}"] in dev.identifiers
    assert rt.started == []  # the connected driver picks the records up without a restart
    light = hub.states.get("light.desk_lamp")
    assert light is not None and light.state == "on"
    assert light.attributes["supported_color_modes"] == ["color_temp", "hs", "xy"]


async def test_a_matter_plug_becomes_a_switch_the_driver_drives(rt):
    from services.homehub.drivers.matter import MatterDriver

    hub = rt.hub
    entry = ConfigEntry("m1", "matter", "Matter")
    hub.config_entries["m1"] = entry
    drv = MatterDriver(hub, entry)
    drv.server_info = {"compressed_fabric_id": FABRIC}
    sent = []

    async def send(command, *, timeout=30.0, **args):
        sent.append((command, args))
        if command == "commission_with_code":
            return dict(PLUG_NODE)
        if command == "read_attribute":
            return {"1/6/0": True}
        return None

    drv._send = send  # type: ignore[method-assign]
    hub.drivers["m1"] = drv
    result = await flows.commission_matter(rt, "MT:Y.K9042C00KCS.U20")
    assert result["entities"] == ["switch.smart_plug"]
    assert hub.states.get("switch.smart_plug").state == "off"
    await hub.call_service("switch", "turn_on", {"entity_id": "switch.smart_plug"})
    assert ("device_command", {"node_id": 81, "endpoint_id": 1, "cluster_id": 6, "command_name": "On",
                               "payload": {}}) in sent
    assert hub.states.get("switch.smart_plug").state == "on"


async def test_matter_without_a_connected_server_is_503(rt):
    with pytest.raises(flows.FlowError) as err:
        await flows.commission_matter(rt, "34970112332")
    assert err.value.status == 503


async def test_a_discovered_matter_device_asks_for_its_code(rt):
    finding = parse_mdns("_matterc._udp.local.", "Q._matterc._udp.local.", "192.0.2.56", 5540,
                         {b"CM": b"1", b"D": b"1", b"VP": b"1+2"}, "AABBCCDDEE02.local.")
    with pytest.raises(flows.NeedsInput) as need:
        await flows.adopt(rt, finding, {})
    assert need.value.needs[0]["key"] == "code"


async def test_the_adopted_finding_is_known_afterwards(rt, monkeypatch):
    async def wiz(host, method):
        if method == "getSystemConfig":
            return {"mac": "aabbcc0a1b2c", "moduleName": "ESP21_SHTW_01"}
        raise DeviceUnavailable("not metered")

    monkeypatch.setattr(flows, "wiz_request", wiz)
    mgr = DiscoveryManager(rt.hub, rt.store, notifier=None)
    mgr.record(parse_wiz({"result": {"mac": "aabbcc0a1b2c", "moduleName": "ESP21_SHTW_01"}}, "192.0.2.42"))
    assert mgr.list(new_only=True)
    result = await flows.adopt(rt, mgr.get("wiz:aabbcc0a1b2c"), {})
    mgr.mark_adopted("wiz:aabbcc0a1b2c", result["entry_id"])
    assert mgr.list(new_only=True) == []
    listed = mgr.list()[0]
    assert listed["known"] is True and listed["entry_id"] == result["entry_id"]
    assert "sensor.wiz_tunable_white_0a1b2c_power" not in rt.hub.entities


def test_entity_ids_never_collide(rt):
    hub = rt.hub
    hub.entities["light.porch"] = EntityEntry("light.porch", "x")
    assert flows.free_entity_id(hub, "light", "porch", set()) == "light.porch_2"
