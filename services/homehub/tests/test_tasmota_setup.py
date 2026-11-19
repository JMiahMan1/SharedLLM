"""Finding, adding and repairing Tasmota devices: HTTP fingerprints, the add flow, passwords and moved addresses.

Every name, address and MAC is made up (192.0.2.0/24 is documentation space); the devices are
tasmota_fakes.FakeTasmota over real HTTP on 127.0.0.1.
"""
from __future__ import annotations

import time

import pytest
from cryptography.fernet import Fernet

from services.homehub import address_follow, discovery, entries, flows
from services.homehub.core import Area, ConfigEntry, DeviceEntry, EntityEntry, Hub
from services.homehub.discovery import DiscoveryManager, parse_tasmota_http
from services.homehub.runtime import Runtime
from services.homehub.store import Store
from services.homehub.tests.tasmota_fakes import SERVER, FakeTasmota

BULB_MAC, PLUG_MAC, OTHER_MAC = "02:00:00:00:0a:01", "02:00:00:00:0b:02", "02:00:00:00:0c:03"
ARP_HEAD = "IP address       HW type     Flags       HW address            Mask     Device\n"


def arp(rows: dict[str, str]) -> str:
    return ARP_HEAD + "".join(f"{ip:<16} 0x1         0x2         {mac}     *        eth0\n" for ip, mac in rows.items())


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
def env(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7")
    hub.store = store
    hub.areas["den"] = Area("den", "Den")
    rt = FakeRuntime(hub, store)
    sent: list[dict] = []

    async def notifier(payload):
        sent.append(payload)
        return True

    mgr = DiscoveryManager(hub, store, notifier=notifier)
    rt.discovery = mgr
    mgr.runtime = rt
    return hub, store, rt, mgr, sent


@pytest.fixture
async def lan():
    """Made-up devices by LAN address; ``probe`` answers for them as ``http_probe`` would."""
    devices: dict[str, tuple[FakeTasmota, str]] = {}

    async def add(ip: str, dev: FakeTasmota) -> FakeTasmota:
        devices[ip] = (dev, await dev.start())
        return dev

    yield devices, add
    for dev, _addr in devices.values():
        await dev.stop()


def wire(mgr: DiscoveryManager, devices: dict, rows: dict[str, str], monkeypatch, *, others=None):
    """Point the manager's neighbour table and HTTP fingerprints at the made-up LAN."""
    asked: list[str] = []
    others = others or {}

    async def probe(host, session=None):
        asked.append(host)
        if host in others:
            return others[host]
        if host not in devices:
            return None
        return await real_probe(devices[host][1])

    monkeypatch.setattr(mgr, "read_arp", lambda: arp(rows))
    monkeypatch.setattr(mgr, "http_probe", probe)

    async def no_name(host):
        return {"192.0.2.51": "Shelf-Bulb.lan"}.get(host)

    monkeypatch.setattr(mgr, "reverse_name", no_name)
    return asked


async def real_probe(address: str):
    """The real fingerprint request, against a made-up device's address (conftest stubs the method)."""
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as s, \
            s.get(f"http://{address}/cm", params={"cmnd": "Status 0"}) as resp:
        return resp.status, {k.lower(): v for k, v in resp.headers.items()}, await resp.text()


# -- recognising Tasmota (pure) ------------------------------------------------------------------

def test_a_locked_device_is_recognised_by_its_401_and_server_header():
    f = parse_tasmota_http("192.0.2.51", "020000000a01", 401, {"server": SERVER},
                           '{"WARNING":"Need user=<username>&password=<password>"}', "Shelf-Bulb.lan")
    assert f["id"] == "tasmota:020000000a01" and f["integration"] == "tasmota" and f["source"] == "http"
    assert f["name"] == "Shelf-Bulb" and f["props"]["locked"] is True
    assert f["props"]["version"] == "15.6.0" and f["model"] == "ESP8266EX"
    # Older firmware: HTTP 200, no Server header, only the warning.
    old = parse_tasmota_http("192.0.2.52", None, 200, {}, '{"WARNING":"Need user=<username>&password=<password>"}')
    assert old["props"]["locked"] is True and old["name"] == "Tasmota at 192.0.2.52"
    assert old["id"] == "tasmota:192.0.2.52"


def test_an_open_device_is_named_from_its_status_and_others_are_not_tasmota():
    dev = FakeTasmota("plug", mac=PLUG_MAC, name="Desk Plug")
    import json

    f = parse_tasmota_http("192.0.2.60", None, 200, {"server": SERVER}, json.dumps(dev.status0()))
    assert f["name"] == "Desk Plug" and f["mac"] == "020000000b02" and f["props"]["locked"] is False
    assert f["props"]["version"] == "15.6.0"
    assert parse_tasmota_http("192.0.2.1", None, 404, {"server": "nginx"}, "<html>Not found</html>") is None
    assert parse_tasmota_http("192.0.2.2", None, 401, {"www-authenticate": 'Basic realm="Login"'}, "") is None
    assert parse_tasmota_http("192.0.2.3", None, 200, {}, '{"WARNING":"something else"}') is None


# -- the discovery loop ------------------------------------------------------------------------------

async def test_a_new_locked_tasmota_is_found_listed_as_needing_its_password_and_told_once(env, lan, monkeypatch):
    _hub, _store, _rt, mgr, sent = env
    devices, add = lan
    await add("192.0.2.51", FakeTasmota("ct", mac=BULB_MAC, password="made-up-pw"))
    rows = {"192.0.2.51": BULB_MAC, "192.0.2.70": OTHER_MAC}
    asked = wire(mgr, devices, rows, monkeypatch)
    await mgr.housekeeping()  # every minute: new neighbours are asked once
    assert sorted(asked) == ["192.0.2.51", "192.0.2.70"]
    found = {f["id"]: f for f in mgr.list(new_only=True)}
    f = found["tasmota:020000000a01"]
    assert f["name"] == "Shelf-Bulb" and f["host"] == "192.0.2.51" and f["adoptable"] is True
    assert f["note"] == "Needs its web password"
    assert [n["key"] for n in f["needs"]] == ["password"] and not f["needs"][0].get("optional")
    assert len(sent) == 1 and sent[0]["once"] == "homehub:new-device:tasmota:020000000a01"
    assert "needs its web password" in sent[0]["body"].lower()
    # The next pass asks nobody again and tells nobody again.
    await mgr.housekeeping()
    assert len(asked) == 2 and len(sent) == 1
    # A scan asks again (keeping the finding fresh), still without a second notice.
    mgr.findings["tasmota:020000000a01"]["last_seen"] = time.time() - 3600
    monkeypatch.setattr(mgr, "ssdp_search", lambda hosts: _none())
    monkeypatch.setattr(mgr, "wiz_sweep", lambda hosts: _none())
    monkeypatch.setattr(mgr, "subnet_hosts", lambda: [])
    result = await mgr.scan()
    assert "192.0.2.51" in asked[2:] and result["found"]["http"] == 0
    assert time.time() - mgr.findings["tasmota:020000000a01"]["last_seen"] < 60 and len(sent) == 1


async def _none():
    return []


async def test_set_up_devices_extenders_and_other_protocols_findings_are_not_asked(env, lan, monkeypatch):
    hub, _store, _rt, mgr, _sent = env
    devices, _add = lan
    entry = ConfigEntry("e1", "wiz", "Porch bulb", {"host": "192.0.2.80"}, unique_id="020000000d04")
    hub.config_entries["e1"] = entry
    mgr.record({"id": "esphome:x", "integration": "esphome", "host": "192.0.2.81", "mac": "020000000d05",
                "ids": [], "props": {}})
    rows = {"192.0.2.80": "02:00:00:00:0d:04", "192.0.2.81": "02:00:00:00:0d:05",
            "192.0.2.90": "02:00:00:00:0e:06", "192.0.2.91": "02:00:00:00:0e:06"}
    asked = wire(mgr, devices, rows, monkeypatch)
    await mgr.housekeeping()
    assert asked == []


async def test_an_open_tasmota_announced_over_mdns_is_fingerprinted(env, lan, monkeypatch):
    _hub, _store, _rt, mgr, _sent = env
    devices, add = lan
    await add("192.0.2.60", FakeTasmota("plug", mac=PLUG_MAC, name="Desk Plug"))
    asked = wire(mgr, devices, {}, monkeypatch)
    assert mgr.handle_mdns("_http._tcp.local.", "desk-plug._http._tcp.local.", "192.0.2.60", 80, {},
                           "desk-plug.local.") is None
    await mgr.housekeeping()
    assert asked == ["192.0.2.60"]
    f = next(f for f in mgr.list() if f["integration"] == "tasmota")
    assert f["id"] == "tasmota:020000000b02" and f["name"] == "Desk Plug" and f["note"] is None
    assert [n.get("optional") for n in f["needs"]] == [True]


async def test_a_dhcp_tasmota_finding_and_its_fingerprint_are_one_device(env, lan, monkeypatch):
    _hub, _store, _rt, mgr, _sent = env
    devices, add = lan
    await add("192.0.2.86", FakeTasmota("plug", mac=PLUG_MAC, password="made-up-pw"))
    mgr.record(discovery.parse_dhcp("192.0.2.86", "020000000b02", "tasmota_0B0B02-1234"))
    wire(mgr, devices, {"192.0.2.86": PLUG_MAC}, monkeypatch)
    await mgr.housekeeping()
    tas = [f for f in mgr.list() if f["integration"] == "tasmota"]
    assert len(tas) == 1 and tas[0]["note"] == "Needs its web password"


# -- adding --------------------------------------------------------------------------------------------

async def test_adding_a_locked_bulb_asks_for_the_password_and_stores_one_entry_with_a_light(env, lan, monkeypatch):
    hub, store, rt, _mgr, _sent = env
    devices, add = lan
    bulb = await add("192.0.2.51", FakeTasmota("rgbcw", mac=BULB_MAC, name="Shelf Bulb", password="made-up-pw"))
    finding = {"id": "tasmota:020000000a01", "integration": "tasmota", "host": devices["192.0.2.51"][1],
               "ids": [], "props": {"locked": True}, "source": "http"}
    with pytest.raises(flows.NeedsInput) as need:
        await flows.adopt(rt, finding, {})
    assert need.value.needs[0]["key"] == "password" and need.value.error is None
    with pytest.raises(flows.NeedsInput) as wrong:
        await flows.adopt(rt, finding, {"input": {"password": "nope"}})
    assert wrong.value.error == "The password was not accepted."
    result = await flows.adopt(rt, finding, {"area_id": "Den", "input": {"password": "made-up-pw"}})
    entry = hub.config_entries[result["entry_id"]]
    assert entry.unique_id == "020000000A01" and entry.title == "Shelf Bulb"
    assert entry.data == {"host": finding["host"], "mac": BULB_MAC, "username": "admin", "password": "made-up-pw"}
    light = hub.entities["light.shelf_bulb"]
    assert light.unique_id == "020000000A01_light_light_0"  # HA's tasmota light id
    assert light.capabilities == {"supported_color_modes": ["color_temp", "hs"]}
    assert not [e for e in hub.entities.values() if e.domain == "switch"]
    assert hub.devices[result["device_id"]].area_id == "den"
    # Stored encrypted with the rest of the entry's data; the list of entries never shows it.
    assert "made-up-pw" not in str(store._exec("SELECT doc FROM config_entries").fetchall())
    assert store.load_config_entries()[0].data["password"] == "made-up-pw"
    listed = entries.list_entries(rt)["entries"][0]
    assert "made-up-pw" not in str(listed)
    assert {f["key"]: f.get("set") for f in listed["reconfigure"] if f["type"] == "password"} == {"password": True}
    # The driver accepts what the flow made.
    from services.homehub.drivers.tasmota import TasmotaDriver

    drv = TasmotaDriver(hub, entry)
    drv._command = lambda c: _status(bulb, c)  # type: ignore[method-assign]
    await drv.async_setup()
    assert hub.states.get("light.shelf_bulb").attributes["supported_color_modes"] == ["color_temp", "hs"]
    # Added twice: refused, pointing at the entry that has it.
    with pytest.raises(flows.FlowError) as dup:
        await flows.adopt(rt, finding, {"input": {"password": "made-up-pw"}})
    assert dup.value.status == 409 and "Shelf Bulb" in str(dup.value)


async def _status(dev: FakeTasmota, command: str):
    return dev.run(command)


async def test_adding_by_address_offers_the_password_field():
    from services.homehub import pairing

    assert [f["key"] for f in pairing.MANUAL["tasmota"]["fields"]] == ["host", "password"]


async def test_a_relay_plug_with_a_light_gets_both(env, monkeypatch):
    _hub, _store, rt, _mgr, _sent = env
    dev = FakeTasmota("ct", mac=PLUG_MAC, name="Fan Light")
    dev.sts["POWER1"] = "ON"
    dev.sts["POWER2"] = dev.sts.pop("POWER")

    async def status(host, password):
        return dev.status0()

    monkeypatch.setattr(flows, "tasmota_status", status)
    result = await flows.adopt(rt, {"id": "tasmota:x", "integration": "tasmota", "host": "192.0.2.61",
                                    "ids": [], "props": {}}, {})
    uids = sorted(rt.hub.entities[e].unique_id for e in result["entities"])
    assert uids == ["020000000B02_light_light_1", "020000000B02_switch_relay_0"]


# -- repairing an entry imported from HA ---------------------------------------------------------------

def imported_hub(*, url: str = "http://192.0.2.244/", reason: str | None = None):
    """An entry as HA's MQTT import left it: no data, the address only on the device, three relays."""
    hub = Hub(time_zone="UTC")
    entry = ConfigEntry("imp1", "tasmota", "Tasmota", {}, source="mqtt")
    entry.state, entry.reason = ("setup_retry", reason) if reason else ("loaded", None)
    hub.config_entries["imp1"] = entry
    hub.devices["d1"] = DeviceEntry(id="d1", name="Desk Plug", config_entries=["imp1"], configuration_url=url,
                                    connections=[["mac", PLUG_MAC]])
    for n in range(3):
        hub.entities[f"switch.desk_plug_{n}"] = EntityEntry(f"switch.desk_plug_{n}", "tasmota",
                                                            f"020000000B02_switch_relay_{n}", "imp1", "d1")
        hub.publish(f"switch.desk_plug_{n}", "unavailable", {})
    return hub, entry


class _Store:
    def __init__(self):
        self.entries: list[str] = []
        self.devices: list[str] = []

    def save_config_entry(self, entry):
        self.entries.append(entry.entry_id)

    def save_device(self, device):
        self.devices.append(device.id)


async def test_an_imported_entry_is_followed_by_mac_from_its_device_address():
    hub, entry = imported_hub()
    hub.store = _Store()
    # The router gave the plug .174; .244 answers for nothing now.
    learn, moves = address_follow.plan(hub, {"192.0.2.174": PLUG_MAC}, set())
    assert learn == [] and [(m.old_ip, m.new_ip) for m in moves] == [("192.0.2.244", "192.0.2.174")]
    address_follow.apply_move(hub, moves[0])
    assert entry.data == {"host": "192.0.2.174"}  # kept on the entry from now on
    assert hub.devices["d1"].configuration_url == "http://192.0.2.174/"
    assert hub.store.entries == ["imp1"] and hub.store.devices == ["d1"]
    assert address_follow.plan(hub, {"192.0.2.174": PLUG_MAC}, set()) == ([], [])
    # Its next move is followed through the entry's own host.
    _learn, moves = address_follow.plan(hub, {"192.0.2.175": PLUG_MAC}, set())
    assert [(m.old_ip, m.new_ip) for m in moves] == [("192.0.2.174", "192.0.2.175")]
    assert address_follow.lost(hub, {}) is True


async def test_the_discovery_loop_follows_it_and_reloads_it(env, monkeypatch):
    _hub, _store, _rt, mgr, sent = env
    hub, entry = imported_hub()
    hub.store = _Store()
    mgr.hub = hub
    mgr.runtime = rt = FakeRuntime(hub, _store)
    monkeypatch.setattr(mgr, "read_arp", lambda: arp({"192.0.2.174": PLUG_MAC}))
    await mgr.follow_addresses()
    assert entry.data["host"] == "192.0.2.174" and rt.stopped == ["imp1"] and rt.started == ["imp1"]
    assert len(sent) == 1 and "192.0.2.174" in sent[0]["body"]


async def test_an_imported_entry_offers_its_address_and_password_and_says_it_needs_the_password():
    hub, entry = imported_hub(reason="AuthRequired: Tasmota 192.0.2.174 needs its web password")
    rt = FakeRuntime(hub, None)
    summary = entries.summary(rt, entry)
    assert summary["needs"] == "password"
    fields = {f["key"]: f for f in summary["reconfigure"]}
    assert list(fields) == ["host", "password"]
    assert fields["host"]["value"] == "192.0.2.244" and fields["password"] == {
        "key": "password", "label": "Web password", "type": "password", "set": False,
        "help": "The password of the device's own web page. Leave empty to keep the stored one."}
    entry.reason = "DeviceUnavailable: Tasmota 192.0.2.244 unreachable"
    assert entries.summary(rt, entry)["needs"] is None


async def test_entering_the_password_repairs_the_imported_entry(lan, tmp_path):
    devices, add = lan
    plug = await add("192.0.2.174", FakeTasmota("plug", mac=PLUG_MAC, password="made-up-pw"))
    hub, entry = imported_hub(url=f"http://{devices['192.0.2.174'][1]}/",
                              reason="AuthRequired: Tasmota needs its web password")

    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    rt = FakeRuntime(hub, store)
    with pytest.raises(entries.EntryError, match="not accepted"):
        await entries.reconfigure_entry(rt, "imp1", {"data": {"password": "wrong"}})
    assert entry.data == {}
    summary = await entries.reconfigure_entry(rt, "imp1", {"data": {"password": "made-up-pw"}})
    assert entry.data == {"password": "made-up-pw", "host": devices["192.0.2.174"][1], "username": "admin"}
    assert rt.stopped == ["imp1"] and rt.started == ["imp1"]
    assert "made-up-pw" not in str(summary)
    assert plug.auth_seen[-1] == "made-up-pw"
    # A device that does not answer is saved anyway (the hub follows it once it is back).
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    closed = f"127.0.0.1:{s.getsockname()[1]}"
    s.close()  # nothing listens there now
    await entries.reconfigure_entry(rt, "imp1", {"data": {"host": closed}})
    assert entry.data["host"] == closed
