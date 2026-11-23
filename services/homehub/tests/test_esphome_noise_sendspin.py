"""ESPHome Noise PSK provisioning on adoption, and Music Assistant Sendspin pairing.

* An echo-dot-assist (hassmic) node starts plaintext: the flow gives it a fresh 32-byte Noise key,
  reconnects with it and saves only the key the node accepted.
* A node that declines runtime key provisioning is still added, unencrypted.
* A node that accepts a key but fails encrypted reconnection is a clear error, never a leaked key.
* Sendspin info and pairing use the satellite's own token, and fail loudly without Music Assistant.

Every address, key and token here is made up.
"""
from __future__ import annotations

import base64
import logging
from typing import ClassVar

import pytest
from aioesphomeapi import APIConnectionError, DeviceInfo

from services.homehub import flows
from services.homehub import music_assistant_server as mas
from services.homehub.core import ConfigEntry, DeviceEntry, EntityEntry, Hub
from services.homehub.discovery import parse_mdns
from services.homehub.drivers.music_assistant import MusicAssistantDriver

MAC = "aabbccddee60"
PSK = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="  # made up, base64 of 32 bytes
PAIRING_TOKEN = "sendspin-pairing-token-made-up"
MA_TOKEN = "music-assistant-hub-token-made-up"


class MemoryStore:
    """No filesystem: the flow's saves are kept in memory for the assertions."""

    def __init__(self) -> None:
        self.entries: dict[str, ConfigEntry] = {}

    def save_config_entry(self, entry: ConfigEntry) -> None:
        self.entries[entry.entry_id] = entry

    def save_device(self, device: DeviceEntry) -> None:
        pass

    def save_entity(self, entity: EntityEntry) -> None:
        pass

    def load_config_entries(self) -> list[ConfigEntry]:
        return list(self.entries.values())


class FakeRuntime:
    def __init__(self, hub: Hub, store: MemoryStore) -> None:
        self.hub, self.store = hub, store
        self.started: list[str] = []
        self.stopped: list[str] = []

    def start_entry(self, entry: ConfigEntry) -> None:
        self.started.append(entry.entry_id)

    async def stop_entry(self, entry_id: str) -> None:
        self.stopped.append(entry_id)


@pytest.fixture
def rt() -> FakeRuntime:
    return FakeRuntime(Hub(time_zone="Etc/GMT+7"), MemoryStore())


def esphome_finding() -> dict:
    return parse_mdns("_esphomelib._tcp.local.", "echo-dot._esphomelib._tcp.local.", "192.0.2.60", 6053,
                      {b"friendly_name": b"Echo Dot", b"mac": MAC.encode(), b"platform": b"ESP32"},
                      "echo-dot.local.")


def hassmic_device(*, project_name: str = "hassmic.echo-dot-assist",
                   voice_flags: int = 0, provisionable: bool = False) -> tuple[DeviceInfo, list]:
    info = DeviceInfo(name="echo-dot-assist", friendly_name="Echo Dot", mac_address="AA:BB:CC:DD:EE:60",
                      esphome_version="2026.6.2", manufacturer="Espressif", model="esp32dev",
                      project_name=project_name, project_version="1.0", webserver_port=80,
                      voice_assistant_feature_flags=voice_flags,
                      api_encryption_provisionable=provisionable)
    return info, []


class FakeAPIClient:
    """Stands in for aioesphomeapi.APIClient in the provisioning helper."""

    instances: ClassVar[list[FakeAPIClient]] = []
    accepts_key: ClassVar[bool] = True
    fail_set_key: ClassVar[bool] = False

    def __init__(self, host: str, port: int, password: str, *, client_info: str | None = None,
                 noise_psk: str | None = None) -> None:
        self.host, self.port, self.password = host, port, password
        self.client_info, self.noise_psk = client_info, noise_psk
        self.key: bytes | None = None
        self.disconnected = False
        FakeAPIClient.instances.append(self)

    async def connect(self, login: bool = True) -> None:
        pass

    async def noise_encryption_set_key(self, key: bytes) -> bool:
        self.key = key
        if FakeAPIClient.fail_set_key:
            raise APIConnectionError("connection dropped mid-provisioning")
        return self.accepts_key

    async def disconnect(self) -> None:
        self.disconnected = True


# -- the provisioning helper ---------------------------------------------------------------

async def test_provision_helper_makes_a_fresh_32_byte_base64_key(monkeypatch):
    import aioesphomeapi

    FakeAPIClient.instances = []
    FakeAPIClient.accepts_key = True
    monkeypatch.setattr(aioesphomeapi, "APIClient", FakeAPIClient)

    psk = await flows.esphome_provision_noise_psk("192.0.2.60", 6053)

    assert psk is not None and len(base64.b64decode(psk)) == 32
    client = FakeAPIClient.instances[-1]
    assert client.key == psk.encode("ascii")
    assert client.client_info == "homehub" and client.noise_psk is None
    assert client.disconnected


async def test_provision_helper_returns_none_when_the_node_declines(monkeypatch):
    import aioesphomeapi

    FakeAPIClient.instances = []
    FakeAPIClient.accepts_key = False
    monkeypatch.setattr(aioesphomeapi, "APIClient", FakeAPIClient)

    assert await flows.esphome_provision_noise_psk("192.0.2.60", 6053) is None
    assert FakeAPIClient.instances[-1].disconnected


# -- adoption -------------------------------------------------------------------------------

async def test_an_echo_node_is_given_a_key_and_saved_with_only_the_verified_one(rt, monkeypatch):
    info, infos = hassmic_device()
    probes: list[str | None] = []
    provisioned: list[tuple[str, int, str]] = []
    key = base64.b64encode(b"k" * 32).decode("ascii")

    async def probe(host, port, psk, password=""):
        probes.append(psk)
        return info, infos

    async def provision(host, port, password=""):
        provisioned.append((host, port, password))
        return key

    monkeypatch.setattr(flows, "esphome_probe", probe)
    monkeypatch.setattr(flows, "esphome_provision_noise_psk", provision)

    result = await flows.adopt(rt, esphome_finding(), {})

    entry = rt.hub.config_entries[result["entry_id"]]
    assert entry.data["noise_psk"] == key
    assert probes == [None, key]  # plaintext first, then the encrypted reconnect that proves the key
    assert provisioned == [("192.0.2.60", 6053, "")]
    saved = [e for e in rt.store.load_config_entries() if e.entry_id == entry.entry_id]
    assert saved and saved[0].data["noise_psk"] == key


async def test_an_echo_node_that_declines_a_key_is_added_plaintext(rt, monkeypatch):
    info, infos = hassmic_device()
    probes: list[str | None] = []
    provisioned: list[str] = []

    async def probe(host, port, psk, password=""):
        probes.append(psk)
        return info, infos

    async def provision(host, port, password=""):
        provisioned.append(host)
        return None  # ordinary plaintext ESPHome firmware: it cannot take a key at runtime

    monkeypatch.setattr(flows, "esphome_probe", probe)
    monkeypatch.setattr(flows, "esphome_provision_noise_psk", provision)

    result = await flows.adopt(rt, esphome_finding(), {})

    assert rt.hub.config_entries[result["entry_id"]].data["noise_psk"] == ""
    assert probes == [None]  # no pointless encrypted reconnect
    assert provisioned == ["192.0.2.60"]


async def test_a_node_that_offers_runtime_provisioning_gets_a_key_without_a_hassmic_name(rt, monkeypatch):
    info, infos = hassmic_device(project_name="Example.RGBWW", provisionable=True)
    probes: list[str | None] = []
    key = base64.b64encode(b"p" * 32).decode("ascii")

    async def probe(host, port, psk, password=""):
        probes.append(psk)
        return info, infos

    async def provision(host, port, password=""):
        return key

    monkeypatch.setattr(flows, "esphome_probe", probe)
    monkeypatch.setattr(flows, "esphome_provision_noise_psk", provision)

    result = await flows.adopt(rt, esphome_finding(), {})

    assert rt.hub.config_entries[result["entry_id"]].data["noise_psk"] == key
    assert probes == [None, key]


async def test_an_ordinary_plain_node_never_tries_provisioning(rt, monkeypatch):
    info, infos = hassmic_device(project_name="Example.RGBWW")
    calls: list[str] = []

    async def probe(host, port, psk, password=""):
        calls.append("probe")
        return info, infos

    async def provision(host, port, password=""):
        raise AssertionError("an ordinary plaintext node must not be provisioned")

    monkeypatch.setattr(flows, "esphome_probe", probe)
    monkeypatch.setattr(flows, "esphome_provision_noise_psk", provision)

    result = await flows.adopt(rt, esphome_finding(), {})

    assert rt.hub.config_entries[result["entry_id"]].data["noise_psk"] == ""
    assert calls == ["probe"]


async def test_a_key_accepted_but_not_reconnectable_is_a_clear_error_without_the_key(rt, monkeypatch, caplog):
    info, infos = hassmic_device()
    key = base64.b64encode(b"z" * 32).decode("ascii")
    caplog.set_level(logging.DEBUG)

    async def probe(host, port, psk, password=""):
        if psk is None:
            return info, infos
        raise OSError("node refused the encrypted reconnect")

    async def provision(host, port, password=""):
        return key

    monkeypatch.setattr(flows, "esphome_probe", probe)
    monkeypatch.setattr(flows, "esphome_provision_noise_psk", provision)

    with pytest.raises(flows.FlowError) as err:
        await flows.adopt(rt, esphome_finding(), {})

    message = str(err.value)
    assert err.value.status == 502
    assert "accepted a new encryption key" in message and "did not reconnect" in message
    assert key not in message
    assert key not in caplog.text
    assert rt.hub.config_entries == {}  # nothing half-adopted


async def test_a_connection_drop_while_provisioning_is_a_clean_502_without_the_key(rt, monkeypatch, caplog):
    import aioesphomeapi

    FakeAPIClient.instances = []
    FakeAPIClient.accepts_key = True
    monkeypatch.setattr(FakeAPIClient, "fail_set_key", True)
    monkeypatch.setattr(aioesphomeapi, "APIClient", FakeAPIClient)
    info, infos = hassmic_device()
    caplog.set_level(logging.DEBUG)

    async def probe(host, port, psk, password=""):
        return info, infos

    monkeypatch.setattr(flows, "esphome_probe", probe)

    with pytest.raises(flows.FlowError) as err:
        await flows.adopt(rt, esphome_finding(), {})

    client = FakeAPIClient.instances[-1]
    assert client.key is not None
    key = client.key.decode("ascii")
    assert err.value.status == 502
    assert "Failed to provision encryption key on Echo Dot" in str(err.value)
    assert "connection dropped mid-provisioning" in str(err.value)
    assert key not in str(err.value)
    assert key not in caplog.text
    assert rt.hub.config_entries == {}  # nothing half-adopted
    assert client.disconnected


# -- Music Assistant Sendspin ----------------------------------------------------------------

class Conn:
    def __init__(self, *, connected: bool = True, result: bool | None = True) -> None:
        self.connected = connected
        self.result = result
        self.server_info = {"base_url": "http://192.0.2.205:8095", "server_id": "srv1"}
        self.sent: list[tuple[str, dict]] = []

    async def command(self, command: str, **args):
        self.sent.append((command, args))
        return self.result


def sendspin_hub(*, token: str | None = PAIRING_TOKEN, disabled_by: str | None = None,
                 noise_psk: str = PSK, connected: bool = True) -> tuple[Hub, ConfigEntry, Conn]:
    hub = Hub(time_zone="Etc/GMT+7")
    entry = ConfigEntry("esp1", "esphome", "Echo Dot", source="zeroconf", unique_id="aa:bb:cc:dd:ee:60",
                        data={"host": "192.0.2.60", "port": 6053, "password": "", "noise_psk": noise_psk,
                              "device_name": "echo-dot-assist"})
    hub.config_entries["esp1"] = entry
    hub.devices["sat1"] = DeviceEntry(id="sat1", name="Echo Dot", config_entries=["esp1"])
    entity_id = "sensor.echo_dot_sendspin_pairing_token"
    hub.entities[entity_id] = EntityEntry(
        entity_id=entity_id, platform="esphome",
        unique_id="AA:BB:CC:DD:EE:60/0/text_sensor/Sendspin pairing token",
        config_entry_id="esp1", device_id="sat1", original_name="Sendspin pairing token",
        disabled_by=disabled_by)
    if token is not None and disabled_by is None:
        hub.states.set(entity_id, token, {})
    ma_entry = ConfigEntry("ma1", "music_assistant", "Music Assistant", state="loaded",
                           data={"url": "http://192.0.2.205:8095", "token": MA_TOKEN})
    hub.config_entries["ma1"] = ma_entry
    conn = Conn(connected=connected)
    driver = MusicAssistantDriver(hub, ma_entry)
    driver.conn = conn
    hub.drivers["ma1"] = driver
    return hub, entry, conn


async def test_sendspin_info_reports_the_address_and_hides_the_token_by_default():
    hub, _entry, _conn = sendspin_hub()

    out = await mas.sendspin_pairing_info(hub, "sat1")

    assert out["ok"] is True and out["token_available"] is True
    assert out["sendspin_address"] == "192.0.2.60:28928"
    assert out["music_assistant_url"] == "http://192.0.2.205:8095"
    assert out["paired"] is False and out["manual_pairing_required"] is False
    assert "error" not in out and out.get("error") is None
    assert "pairing_token" not in out
    assert PAIRING_TOKEN not in str(out)

    admin = await mas.sendspin_pairing_info(hub, "sat1", include_token=True)
    assert admin["pairing_token"] == PAIRING_TOKEN
    assert "error" not in admin and admin.get("error") is None


async def test_pairing_registers_the_satellite_and_never_returns_the_token():
    hub, _entry, conn = sendspin_hub()

    out = await mas.pair_sendspin_device(hub, "sat1")

    assert out["ok"] is True and out["paired"] is True
    assert "error" not in out and out.get("error") is None
    assert out["sendspin_address"] == "192.0.2.60:28928"
    assert conn.sent == [(mas.SENDSPIN_PAIR_COMMAND,
                          {"address": "192.0.2.60:28928", "token": PAIRING_TOKEN})]
    assert PAIRING_TOKEN not in str(out)


async def test_sendspin_without_music_assistant_fails_fast_naming_the_integration():
    hub = Hub(time_zone="Etc/GMT+7")
    hub.devices["sat1"] = DeviceEntry(id="sat1", name="Echo Dot")

    with pytest.raises(mas.SendspinError) as err:
        await mas.sendspin_pairing_info(hub, "sat1")
    assert err.value.status == 503
    assert "Integrations > Music Assistant" in str(err.value)

    with pytest.raises(mas.SendspinError) as err:
        await mas.pair_sendspin_device(hub, "sat1")
    assert "Integrations > Music Assistant" in str(err.value)


async def test_sendspin_with_a_disconnected_music_assistant_fails_loudly():
    hub, _entry, _conn = sendspin_hub(connected=False)

    with pytest.raises(mas.SendspinError) as err:
        await mas.sendspin_pairing_info(hub, "sat1")
    assert "Music Assistant" in str(err.value)


async def test_a_missing_device_is_a_404_and_a_missing_token_explains_why():
    hub, _entry, _conn = sendspin_hub()

    token, why = mas.find_sendspin_token(hub, "nowhere")
    assert token is None and "no device" in (why or "").lower()
    with pytest.raises(mas.SendspinError) as err:
        await mas.sendspin_pairing_info(hub, "nowhere")
    assert err.value.status == 404 and "no device" in str(err.value).lower()
    with pytest.raises(mas.SendspinError) as err:
        await mas.pair_sendspin_device(hub, "nowhere")
    assert err.value.status == 404 and "no device" in str(err.value).lower()

    hub, _entry, conn = sendspin_hub(token=None)
    token, why = mas.find_sendspin_token(hub, "sat1")
    assert token is None and "not published" in (why or "").lower()
    out = await mas.sendspin_pairing_info(hub, "sat1")
    assert out["ok"] is False and out["token_available"] is False
    assert out["sendspin_address"] == "192.0.2.60:28928"
    assert "not published" in (out["error"] or "").lower()

    paired = await mas.pair_sendspin_device(hub, "sat1")
    assert paired["ok"] is False and paired["paired"] is False and paired["manual_pairing_required"] is True
    assert "not published" in (paired["error"] or "").lower()
    assert conn.sent == []


async def test_a_disabled_token_entity_says_to_enable_it_and_that_the_node_is_unencrypted():
    hub, _entry, _conn = sendspin_hub(disabled_by="integration", noise_psk="")

    token, why = mas.find_sendspin_token(hub, "sat1")

    assert token is None
    assert "disabled" in (why or "")
    assert "not encrypted" in (why or "")
