"""New-device discovery: parsing what devices announce, matching the registry, notices, and the API.

Every address, MAC and id here is made up (documentation ranges, aa:bb:... MACs).
"""
from __future__ import annotations

import asyncio
import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import discovery
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import ConfigEntry, DeviceEntry, EntityEntry, Hub
from services.homehub.discovery import DiscoveryManager, KnownIndex, parse_mdns, parse_ssdp, parse_wiz
from services.homehub.runtime import Runtime
from services.homehub.store import Store

SECRET = "internal-secret"
ESP_TYPE = "_esphomelib._tcp.local."


def esphome_service(name="porch-bulb", mac="aabbccddee01", encrypted=True, host="192.0.2.34"):
    props = {b"friendly_name": b"Porch Bulb", b"version": b"2026.6.2", b"mac": mac.encode(),
             b"platform": b"ESP8266", b"board": b"esp07s", b"network": b"wifi", b"project_name": b"Example.RGBWW"}
    if encrypted:
        props[b"api_encryption"] = b"Noise_NNpsk0_25519_ChaChaPoly_SHA256"
    return (ESP_TYPE, f"{name}.{ESP_TYPE}", host, 6053, props, f"{name}.local.")


ROKU_REPLY = (
    "HTTP/1.1 200 OK\r\nCache-Control: max-age=3600\r\nST: roku:ecp\r\n"
    "USN: uuid:roku:ecp:X00000000001\r\nLOCATION: http://192.0.2.96:8060/\r\n"
    "Server: Roku/15.3.4 UPnP/1.0 Roku/15.3.4\r\n\r\n"
)
ROKU_DIAL = (
    "HTTP/1.1 200 OK\r\nST: urn:dial-multiscreen-org:service:dial:1\r\n"
    "USN: uuid:29780000-0000-0000-0000-000000000001::urn:dial-multiscreen-org:service:dial:1\r\n"
    "LOCATION: http://192.0.2.96:8060/dial/dd.xml\r\nServer: Roku/15.3.4 UPnP/1.0 Roku/15.3.4\r\n\r\n"
)
RENDERER = (
    "HTTP/1.1 200 OK\r\nST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
    "USN: uuid:0a0b0c0d-0000-1000-8000-000000000002::urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
    "LOCATION: http://192.0.2.70:9197/dmr\r\nServer: Linux UPnP/1.0\r\n\r\n"
)
ROKU_DESCRIPTION = """<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>
<friendlyName>Den Roku</friendlyName><manufacturer>Roku</manufacturer><modelName>Roku Ultra</modelName>
<serialNumber>X00000000001</serialNumber><UDN>uuid:29780000-0000-0000-0000-000000000001</UDN></device></root>"""


class FakeRuntime(Runtime):
    """The real Runtime, except that entries are recorded instead of driven."""

    def __init__(self, hub, store):
        super().__init__(hub, store)
        self.started: list[str] = []
        self.stopped: list[str] = []

    def start_entry(self, entry):
        self.started.append(entry.entry_id)

    async def stop_entry(self, entry_id):
        self.stopped.append(entry_id)


@pytest.fixture
def env(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7")
    hub.store = store
    runtime = FakeRuntime(hub, store)
    sent: list[dict] = []

    async def notifier(payload):
        sent.append(payload)
        return True

    mgr = DiscoveryManager(hub, store, notifier=notifier)
    runtime.discovery = mgr
    return hub, store, runtime, mgr, sent


def add_entry(hub, entry_id, domain, *, unique_id=None, data=None, source="import"):
    entry = ConfigEntry(entry_id, domain, f"{domain} {entry_id}", data=data or {}, unique_id=unique_id, source=source)
    hub.config_entries[entry_id] = entry
    return entry


# -- parsing ------------------------------------------------------------------------

def test_an_encrypted_esphome_node_is_read_from_its_txt_record():
    f = parse_mdns(*esphome_service())
    assert f["id"] == "esphome:aabbccddee01"
    assert f["integration"] == "esphome"
    assert f["name"] == "Porch Bulb"
    assert f["host"] == "192.0.2.34" and f["port"] == 6053
    assert f["props"]["encrypted"] is True
    assert f["props"]["device_name"] == "porch-bulb"


def test_matter_homekit_cast_and_tasmota_announcements():
    matter = parse_mdns("_matterc._udp.local.", "0011223344556677._matterc._udp.local.", "192.0.2.56", 5540,
                        {b"CM": b"1", b"D": b"2441", b"VP": b"4107+8869", b"DN": b"Desk Lamp"}, "AABBCCDDEE02.local.")
    assert matter["id"] == "matter:aabbccddee02"
    assert matter["props"]["commissioning_open"] is True
    assert matter["name"] == "Desk Lamp"

    closed = parse_mdns("_matterc._udp.local.", "X._matterc._udp.local.", "192.0.2.57", 5540,
                        {b"CM": b"0", b"D": b"100", b"VP": b"4107+1"}, "host-without-mac.local.")
    assert closed["id"] == "matter:4107-1-100"
    assert closed["props"]["commissioning_open"] is False

    unpaired = parse_mdns("_hap._tcp.local.", "Thermostat._hap._tcp.local.", "192.0.2.60", 80,
                          {b"sf": b"1", b"id": b"AA:BB:CC:00:11:22", b"md": b"T9", b"ci": b"9"}, "t.local.")
    assert unpaired["id"] == "homekit_controller:aa:bb:cc:00:11:22"
    paired = parse_mdns("_hap._tcp.local.", "Other._hap._tcp.local.", "192.0.2.61", 80,
                        {b"sf": b"0", b"id": b"AA:BB:CC:00:11:23"}, "o.local.")
    assert paired is None

    cast = parse_mdns("_googlecast._tcp.local.", "x._googlecast._tcp.local.", "192.0.2.88", 8009,
                      {b"id": b"11112222333344445555666677778888", b"fn": b"Den TV", b"md": b"Chromecast"}, "c.local.")
    assert cast["id"] == "cast:11112222-3333-4444-5555-666677778888"
    assert cast["name"] == "Den TV"

    tas = parse_mdns("_http._tcp.local.", "tasmota-0A1B2C-1234._http._tcp.local.", "192.0.2.90", 80, {},
                     "tasmota-0A1B2C-1234.local.")
    assert tas["integration"] == "tasmota"
    printer = parse_mdns("_http._tcp.local.", "Printer._http._tcp.local.", "192.0.2.91", 80, {}, "printer.local.")
    assert printer is None


def test_ssdp_replies_and_the_description():
    f = parse_ssdp(ROKU_REPLY, "192.0.2.96")
    assert f["id"] == "roku:x00000000001"
    assert f["props"]["serial"] == "X00000000001"
    discovery.apply_description(f, ROKU_DESCRIPTION)
    assert f["name"] == "Den Roku" and f["model"] == "Roku Ultra"
    assert "29780000-0000-0000-0000-000000000001" in f["ids"]
    assert parse_ssdp(RENDERER, "192.0.2.70")["integration"] == "dlna_dmr"
    assert parse_ssdp("HTTP/1.1 200 OK\r\nST: upnp:rootdevice\r\nServer: Linux\r\n\r\n", "192.0.2.5") is None


def test_a_wiz_reply_names_the_bulb_as_ha_did():
    f = parse_wiz({"method": "getSystemConfig", "result": {"mac": "aabbcc0a1b2c", "moduleName": "ESP25_SHRGB_01",
                                                          "fwVersion": "1.30.40"}}, "192.0.2.42")
    assert f["id"] == "wiz:aabbcc0a1b2c"
    assert f["name"] == "WiZ RGB Tunable 0A1B2C"
    assert parse_wiz({"result": {"moduleName": "x"}}, "192.0.2.43") is None


# -- the registry ---------------------------------------------------------------------

def test_known_devices_match_by_entry_id_mac_identifier_entity_and_host(env):
    hub, *_ = env
    add_entry(hub, "wiz1", "wiz", unique_id="aabbcc0a1b2c", data={"host": "192.0.2.42"})
    add_entry(hub, "esp1", "esphome", unique_id="aa:bb:cc:dd:ee:01")
    add_entry(hub, "cast1", "cast", data={"known_hosts": []})
    hub.entities["media_player.den"] = EntityEntry("media_player.den", "cast",
                                                    unique_id="11112222-3333-4444-5555-666677778888",
                                                    config_entry_id="cast1")
    add_entry(hub, "roku1", "roku", data={"host": "192.0.2.96"})
    hub.devices["d1"] = DeviceEntry(id="d1", config_entries=["esp1"], connections=[["mac", "aa:bb:cc:dd:ee:03"]])
    index = KnownIndex(hub)
    assert index.match({"mac": "aabbcc0a1b2c", "ids": []}) == "wiz1"
    assert index.match({"mac": "aabbccddee01", "ids": []}) == "esp1"
    assert index.match({"mac": "aabbccddee03", "ids": []}) == "esp1"
    assert index.match({"ids": ["11112222-3333-4444-5555-666677778888"]}) == "cast1"
    assert index.match({"ids": ["X9"], "host": "192.0.2.96"}) == "roku1"
    assert index.match({"ids": ["X9"], "host": "192.0.2.200"}) is None


def test_a_device_ha_was_told_to_ignore_stays_ignored(env):
    hub, _store, _rt, mgr, _sent = env
    add_entry(hub, "ign", "esphome", unique_id="aa:bb:cc:dd:ee:01", source="ignore")
    mgr.handle_mdns(*esphome_service())
    pub = mgr.list()[0]
    assert pub["known"] is False and pub["ignored"] is True and pub["new"] is False


# -- the manager ----------------------------------------------------------------------

async def test_new_devices_are_told_once_and_known_ones_never(env):
    hub, _store, _rt, mgr, sent = env
    add_entry(hub, "esp-known", "esphome", unique_id="aa:bb:cc:dd:ee:09")
    mgr.handle_mdns(*esphome_service())
    mgr.handle_mdns(*esphome_service(name="known-node", mac="aabbccddee09", host="192.0.2.35"))
    assert await mgr.flush_notices() == 1
    assert len(sent) == 1
    notice = sent[0]
    assert notice["user"] == "@admins" and notice["category"] == "admin"
    assert notice["once"] == "homehub:new-device:esphome:aabbccddee01"
    assert "Porch Bulb" in notice["title"]
    assert notice["data"]["url"] == "/devices"
    assert await mgr.flush_notices() == 0
    assert len(sent) == 1


async def test_many_new_devices_are_one_summary_and_closed_matter_windows_are_quiet(env):
    _hub, _store, _rt, mgr, sent = env
    for i in range(5):
        mgr.handle_mdns(*esphome_service(name=f"node-{i}", mac=f"aabbccddee1{i}", host=f"192.0.2.{10 + i}"))
    mgr.handle_mdns("_matterc._udp.local.", "Q._matterc._udp.local.", "192.0.2.57", 5540,
                    {b"CM": b"0", b"D": b"1", b"VP": b"1+2"}, "nomac.local.")
    assert await mgr.flush_notices() == 5
    assert len(sent) == 1
    assert sent[0]["title"] == "5 new devices found"


async def test_findings_persist_ignores_and_expire(env, tmp_path):
    hub, store, _rt, mgr, _sent = env
    mgr.handle_mdns(*esphome_service())
    assert mgr.set_ignored("esphome:aabbccddee01")["ignored"] is True
    mgr.save()
    again = DiscoveryManager(hub, store)
    again.load()
    assert "esphome:aabbccddee01" in again.findings
    assert again.list(new_only=True) == []
    assert again.list()[0]["ignored"] is True
    again.set_ignored("esphome:aabbccddee01", False)
    assert again.list(new_only=True)[0]["id"] == "esphome:aabbccddee01"
    again.findings["esphome:aabbccddee01"]["last_seen"] = time.time() - discovery.EXPIRE_S - 5
    await again.housekeeping()
    assert again.findings == {}


async def test_a_scan_ranks_one_finding_per_address_and_reads_the_description(env, monkeypatch):
    _hub, _store, _rt, mgr, sent = env

    async def ssdp(hosts):
        return [(ROKU_DIAL, "192.0.2.96"), (ROKU_REPLY, "192.0.2.96"), (RENDERER, "192.0.2.70")]

    async def description(url):
        return ROKU_DESCRIPTION if url.startswith("http://192.0.2.96") else None

    async def wiz(hosts):
        return [({"method": "getSystemConfig", "result": {"mac": "aabbcc0a1b2c", "moduleName": "ESP21_SHTW_01"}},
                 "192.0.2.42")]

    monkeypatch.setattr(mgr, "ssdp_search", ssdp)
    monkeypatch.setattr(mgr, "fetch_description", description)
    monkeypatch.setattr(mgr, "wiz_sweep", wiz)
    monkeypatch.setattr(mgr, "subnet_hosts", lambda: ["192.0.2.1"])
    result = await mgr.scan()
    assert result["found"]["ssdp"] == 2 and result["found"]["wiz"] == 1
    ids = {f["id"]: f for f in mgr.list()}
    assert ids["roku:x00000000001"]["name"] == "Den Roku"
    assert ids["dlna_dmr:0a0b0c0d-0000-1000-8000-000000000002"]["adoptable"] is True
    assert ids["wiz:aabbcc0a1b2c"]["adoptable"] is True
    # the renderer is quiet; the Roku and the bulb are told
    assert sorted(p["tag"] for p in sent) == ["admin:new-device:roku:x00000000001",
                                               "admin:new-device:wiz:aabbcc0a1b2c"]
    again = await mgr.scan()
    assert "skipped" in again


async def test_the_mdns_callback_resolves_on_the_loop(env, monkeypatch):
    _hub, _store, _rt, mgr, _sent = env
    mgr._loop = asyncio.get_running_loop()
    seen = []

    async def resolve(stype, name):
        seen.append((stype, name))

    monkeypatch.setattr(mgr, "_resolve", resolve)

    class Change:
        name = "Added"

    mgr._on_mdns(None, ESP_TYPE, f"porch-bulb.{ESP_TYPE}", Change())
    await asyncio.sleep(0.01)
    assert seen == [(ESP_TYPE, f"porch-bulb.{ESP_TYPE}")]


# -- the API ----------------------------------------------------------------------------

@pytest.fixture
def client(env):
    hub, store, runtime, mgr, _sent = env
    app = build_app(runtime, Auth(store, SECRET))
    return TestClient(app), hub, runtime, mgr


def auth_headers():
    return {"Authorization": f"Bearer {SECRET}"}


def test_the_api_is_for_admins_only(client):
    c, *_ = client
    assert c.get("/hub/discovered").status_code == 401
    assert c.get("/hub/discovered", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.post("/hub/matter/commission", json={"code": "x"}).status_code == 401


def test_list_ignore_and_unknown_ids(client):
    c, _hub, _rt, mgr = client
    mgr.handle_mdns(*esphome_service())
    body = c.get("/hub/discovered", headers=auth_headers()).json()
    assert [d["id"] for d in body["devices"]] == ["esphome:aabbccddee01"]
    dev = body["devices"][0]
    assert dev["new"] is True and dev["adoptable"] is True
    assert dev["needs"][0]["key"] == "noise_psk"
    r = c.post("/hub/discovered/esphome:aabbccddee01/ignore", headers=auth_headers())
    assert r.json()["ignored"] is True
    assert c.get("/hub/discovered?new=1", headers=auth_headers()).json()["devices"] == []
    assert c.post("/hub/discovered/nope/ignore", headers=auth_headers()).status_code == 404
    assert c.post("/hub/discovered/nope/adopt", headers=auth_headers(), json={}).status_code == 404


def test_an_encrypted_node_asks_for_its_key_before_connecting(client, monkeypatch):
    from services.homehub import flows

    c, _hub, _rt, mgr = client
    mgr.handle_mdns(*esphome_service())

    async def never(*a, **k):
        raise AssertionError("must not connect without a key")

    monkeypatch.setattr(flows, "esphome_probe", never)
    r = c.post("/hub/discovered/esphome:aabbccddee01/adopt", headers=auth_headers(), json={})
    assert r.status_code == 200
    assert r.json()["status"] == "needs_input"
    assert r.json()["needs"][0]["key"] == "noise_psk"


def test_a_homekit_accessory_asks_for_its_setup_code(client):
    c, _hub, _rt, mgr = client
    mgr.handle_mdns("_hap._tcp.local.", "T._hap._tcp.local.", "192.0.2.60", 80,
                    {b"sf": b"1", b"id": b"AA:BB:CC:00:11:22", b"ff": b"0"}, "t.local.")
    listed = {d["id"]: d for d in c.get("/hub/discovered", headers=auth_headers()).json()["devices"]}
    assert listed["homekit_controller:aa:bb:cc:00:11:22"]["adoptable"] is True
    assert listed["homekit_controller:aa:bb:cc:00:11:22"]["needs"][0]["key"] == "code"
    r = c.post("/hub/discovered/homekit_controller:aa:bb:cc:00:11:22/adopt", headers=auth_headers(), json={})
    assert r.status_code == 200
    assert r.json()["status"] == "needs_input" and r.json()["needs"][0]["key"] == "code"


def test_scan_is_reachable(client, monkeypatch):
    c, _hub, _rt, mgr = client

    async def scan(reason="x"):
        return {"reason": reason, "found": {}}

    monkeypatch.setattr(mgr, "scan", scan)
    r = c.post("/hub/discovered/scan", headers=auth_headers())
    assert r.status_code == 200 and r.json()["scan"]["reason"] == "on demand"


def test_discovery_not_running_is_503(env):
    _hub, store, runtime, _mgr, _sent = env
    runtime.discovery = None
    c = TestClient(build_app(runtime, Auth(store, SECRET)))
    assert c.get("/hub/discovered", headers=auth_headers()).status_code == 503
