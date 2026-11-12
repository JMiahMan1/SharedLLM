"""Pairing flows (HomeKit, Android TV, Samsung, LG, DLNA, Reolink, ESPHome builder) with fake transports.

Each flow must create what its driver expects; the driver is built on the new
records where that is cheap to check. Every address, id, key and code here is made up.
"""
from __future__ import annotations

import asyncio

import pytest
from aiohomekit.exceptions import AuthenticationError, MaxPeersError

from services.homehub import flows, pairing
from services.homehub.discovery import parse_mdns, parse_ssdp
from services.homehub.drivers.homekit import HomeKitDriver
from services.homehub.drivers.reolink import ReolinkDriver
from services.homehub.tests.test_flows import FakeRuntime, rt  # noqa: F401 (fixture)
from services.homehub.tests.test_homekit_services import BRIDGE, FakePairing

HK_ID = "1A:2B:3C:4D:5E:6F"


@pytest.fixture(autouse=True)
def _clean_sessions():
    pairing.SESSIONS.clear()
    yield
    for sid in list(pairing.SESSIONS):
        pairing.drop_session(sid)


def homekit_finding():
    return parse_mdns("_hap._tcp.local.", "Porch Bridge._hap._tcp.local.", "192.0.2.60", 51826,
                      {b"sf": b"1", b"id": HK_ID.encode(), b"ff": b"1", b"ci": b"2", b"c#": b"4", b"md": b"Bridge"},
                      "bridge.local.")


# -- HomeKit ----------------------------------------------------------------------------

async def test_homekit_pairs_with_the_setup_code_and_creates_ha_entities(rt, monkeypatch):  # noqa: F811
    calls = []

    async def pair(finding, code, alias):
        calls.append(code)
        if code != "123-45-678":
            raise AuthenticationError("wrong code")
        return ({"AccessoryPairingID": HK_ID, "AccessoryIP": "192.0.2.60", "AccessoryPort": 51826,
                 "Connection": "IP", "iOSPairingId": "made-up-controller", "iOSDeviceLTSK": "00", "AccessoryLTPK": "00"},
                BRIDGE)

    monkeypatch.setattr(pairing, "homekit_pair", pair)
    finding = homekit_finding()
    assert flows.needs_for(finding)[0]["key"] == "code"
    with pytest.raises(flows.NeedsInput):
        await flows.adopt(rt, finding, {})
    with pytest.raises(flows.NeedsInput) as need:
        await flows.adopt(rt, finding, {"input": {"code": "1234"}})
    assert "8 digits" in need.value.error
    with pytest.raises(flows.NeedsInput) as need:
        await flows.adopt(rt, finding, {"input": {"code": "111-11-112"}})
    assert need.value.error == "That code was not accepted."
    result = await flows.adopt(rt, finding, {"name": "Porch bridge", "area_id": "Porch",
                                             "input": {"code": "12345678"}})
    assert calls[-1] == "123-45-678"
    hub = rt.hub
    entry = hub.config_entries[result["entry_id"]]
    assert entry.domain == "homekit_controller" and entry.unique_id == HK_ID.lower()
    assert entry.data["iOSPairingId"] == "made-up-controller"
    assert rt.started == [entry.entry_id]
    devices = [d for d in hub.devices.values() if entry.entry_id in d.config_entries]
    assert len(devices) == len(BRIDGE)
    top = [d for d in devices if d.via_device_id is None]
    assert len(top) == 1 and top[0].name_by_user == "Porch bridge" and top[0].area_id == "porch"
    light = next(e for e in hub.entities.values() if e.unique_id == f"{HK_ID}_2_10")
    assert light.domain == "light"
    assert hub.devices[light.device_id].name == "Porch Light"
    # a second attempt is refused
    with pytest.raises(flows.FlowError) as err:
        await flows.adopt(rt, finding, {"input": {"code": "12345678"}})
    assert err.value.status == 409
    # and the driver drives what was created
    drv = HomeKitDriver(hub, entry)
    fake = FakePairing(BRIDGE)

    async def make_pairing():
        return fake

    drv._make_pairing = make_pairing
    await drv.async_setup()
    assert hub.states.get(light.entity_id).state == "on"


async def test_homekit_says_why_it_cannot_pair(rt, monkeypatch):  # noqa: F811
    async def pair(finding, code, alias):
        raise MaxPeersError("full")

    monkeypatch.setattr(pairing, "homekit_pair", pair)
    with pytest.raises(flows.FlowError) as err:
        await flows.adopt(rt, homekit_finding(), {"input": {"code": "123-45-678"}})
    assert err.value.status == 409 and "Home app" in str(err.value)


# -- Android TV Remote ------------------------------------------------------------------

class FakeATV:
    def __init__(self):
        self.pins = []
        self.closed = False
        self.fail_with: Exception | None = None

    async def async_finish_pairing(self, pin):
        self.pins.append(pin)
        if self.fail_with is not None:
            raise self.fail_with
        if pin != "A1B2C3":
            from androidtvremote2 import InvalidAuth

            raise InvalidAuth("wrong")

    def disconnect(self):
        self.closed = True


def atv_finding():
    return parse_mdns("_androidtvremote2._tcp.local.", "Den TV._androidtvremote2._tcp.local.", "192.0.2.80", 6466,
                      {b"bt": b"AA:BB:CC:00:00:80"}, "den-tv.local.")


async def test_android_tv_pairs_with_the_code_on_screen(rt, monkeypatch):  # noqa: F811
    remotes = []
    seen_certs = []

    async def start(host, cert, key):
        seen_certs.append(cert)
        remotes.append(FakeATV())
        return {"remote": remotes[-1], "name": "Den TV", "mac": "AA:BB:CC:00:00:80", "cert_pem": cert or "CERT",
                "key_pem": key or "KEY", "tmpdir": None}

    monkeypatch.setattr(pairing, "atv_start", start)
    finding = atv_finding()
    with pytest.raises(flows.NeedsInput) as need:
        await flows.adopt(rt, finding, {})
    session = need.value.session
    assert session and need.value.needs[0]["key"] == "pin"
    with pytest.raises(flows.NeedsInput) as need:
        await flows.adopt(rt, finding, {"input": {"session": session, "pin": "000000"}})
    assert need.value.error == "That code was not accepted." and need.value.session == session
    result = await flows.adopt(rt, finding, {"input": {"session": session, "pin": "A1B2C3"}})
    entry = rt.hub.config_entries[result["entry_id"]]
    assert entry.unique_id == "aa:bb:cc:00:00:80"
    assert entry.data == {"host": "192.0.2.80", "name": "Den TV", "mac": "aa:bb:cc:00:00:80", "cert_pem": "CERT",
                          "key_pem": "KEY"}
    assert sorted(e.domain for e in rt.hub.entities.values()) == ["media_player", "remote"]
    assert all(e.unique_id == "aa:bb:cc:00:00:80" for e in rt.hub.entities.values())
    assert session not in pairing.SESSIONS
    # the next TV reuses the certificate the TVs already trust
    other = {**atv_finding(), "id": "androidtv_remote:other", "host": "192.0.2.81"}
    remotes.clear()

    async def start2(host, cert, key):
        seen_certs.append(cert)
        remotes.append(FakeATV())
        return {"remote": remotes[-1], "name": "Bedroom TV", "mac": "AA:BB:CC:00:00:81", "cert_pem": cert,
                "key_pem": key, "tmpdir": None}

    monkeypatch.setattr(pairing, "atv_start", start2)
    with pytest.raises(flows.NeedsInput):
        await flows.adopt(rt, other, {})
    assert seen_certs[-1] == "CERT"


async def test_android_tv_restarts_a_pairing_the_tv_closed(rt, monkeypatch):  # noqa: F811
    from androidtvremote2 import ConnectionClosed

    made = []

    async def start(host, cert, key):
        made.append(FakeATV())
        return {"remote": made[-1], "name": "Den TV", "mac": "AA:BB:CC:00:00:80", "cert_pem": "C", "key_pem": "K",
                "tmpdir": None}

    monkeypatch.setattr(pairing, "atv_start", start)
    finding = atv_finding()
    with pytest.raises(flows.NeedsInput) as need:
        await flows.adopt(rt, finding, {})
    made[0].fail_with = ConnectionClosed("cancelled on the TV")
    with pytest.raises(flows.NeedsInput) as again:
        await flows.adopt(rt, finding, {"input": {"session": need.value.session, "pin": "A1B2C3"}})
    assert again.value.session != need.value.session and "new code" in again.value.error
    assert made[0].closed and len(made) == 2


async def test_a_session_from_another_device_or_gone_is_refused(rt, monkeypatch):  # noqa: F811
    with pytest.raises(flows.FlowError) as err:
        await flows.adopt(rt, atv_finding(), {"input": {"session": "nope", "pin": "1"}})
    assert err.value.status == 410


# -- Samsung ----------------------------------------------------------------------------

def samsung_finding():
    text = ("HTTP/1.1 200 OK\r\nST: urn:samsung.com:device:RemoteControlReceiver:1\r\n"
            "USN: uuid:0a0b0c0d-0000-1000-8000-0000000000aa::urn:samsung.com:device:RemoteControlReceiver:1\r\n"
            "LOCATION: http://192.0.2.70:7676/rcr/\r\n\r\n")
    return parse_ssdp(text, "192.0.2.70")


INFO = {"device": {"id": "uuid:0a0b0c0d-0000-1000-8000-0000000000aa", "name": "Den TV", "modelName": "QN00X",
                   "wifiMac": "aa:bb:cc:00:00:70", "TokenAuthSupport": "true"}}


async def test_samsung_waits_for_allow_then_stores_the_token(rt, monkeypatch):  # noqa: F811
    allowed = asyncio.Event()

    async def probe(host):
        return {"method": "websocket", "port": 8002, "info": INFO}

    async def ws_pair(host, port):
        await allowed.wait()
        return "made-up-token"

    monkeypatch.setattr(pairing, "samsung_probe", probe)
    monkeypatch.setattr(pairing, "samsung_ws_pair", ws_pair)
    finding = samsung_finding()
    with pytest.raises(flows.Pending) as wait:
        await flows.adopt(rt, finding, {})
    assert wait.value.step == "accept_on_tv"
    with pytest.raises(flows.Pending):
        await flows.adopt(rt, finding, {"input": {"session": wait.value.session}})
    allowed.set()
    await asyncio.sleep(0)
    result = await flows.adopt(rt, finding, {"input": {"session": wait.value.session}})
    entry = rt.hub.config_entries[result["entry_id"]]
    assert entry.unique_id == "0a0b0c0d-0000-1000-8000-0000000000aa"
    assert entry.data["method"] == "websocket" and entry.data["port"] == 8002
    assert entry.data["token"] == "made-up-token" and entry.data["mac"] == "aa:bb:cc:00:00:70"
    assert {e.domain: e.unique_id for e in rt.hub.entities.values()} == {
        "media_player": "0a0b0c0d-0000-1000-8000-0000000000aa", "remote": "0a0b0c0d-0000-1000-8000-0000000000aa"}


async def test_samsung_deny_is_reported(rt, monkeypatch):  # noqa: F811
    from samsungtvws.exceptions import UnauthorizedError

    async def probe(host):
        return {"method": "legacy", "port": 55000, "info": {}}

    async def legacy(host, port):
        raise UnauthorizedError("denied")

    monkeypatch.setattr(pairing, "samsung_probe", probe)
    monkeypatch.setattr(pairing, "samsung_legacy_pair", legacy)
    finding = samsung_finding()
    with pytest.raises(flows.Pending) as wait:
        await flows.adopt(rt, finding, {})
    await asyncio.sleep(0)
    with pytest.raises(flows.FlowError) as err:
        await flows.adopt(rt, finding, {"input": {"session": wait.value.session}})
    assert err.value.status == 409 and "Deny" in str(err.value)


async def test_samsung_encrypted_pairs_with_the_pin(rt, monkeypatch):  # noqa: F811
    class FakeEncrypted:
        async def finish(self, pin):
            return ("tok", "sess") if pin == "1234" else None

        async def close(self):
            pass

    async def probe(host):
        return {"method": "encrypted", "port": 8000, "info": {}}

    async def start(host):
        return FakeEncrypted()

    monkeypatch.setattr(pairing, "samsung_probe", probe)
    monkeypatch.setattr(pairing, "samsung_encrypted_start", start)
    finding = samsung_finding()
    with pytest.raises(flows.NeedsInput) as need:
        await flows.adopt(rt, finding, {})
    with pytest.raises(flows.NeedsInput) as again:
        await flows.adopt(rt, finding, {"input": {"session": need.value.session, "pin": "0000"}})
    assert again.value.error == "That PIN was not accepted."
    result = await flows.adopt(rt, finding, {"input": {"session": need.value.session, "pin": "1234"}})
    data = rt.hub.config_entries[result["entry_id"]].data
    assert data["method"] == "encrypted" and data["token"] == "tok" and data["session_id"] == "sess"


# -- LG webOS ---------------------------------------------------------------------------

async def test_webos_waits_for_accept_and_stores_the_key_as_client_secret(rt, monkeypatch):  # noqa: F811
    async def pair(host):
        return "k" * 32, {"uuid": "0C0B0A00-0000-0000-0000-0000000000BB", "model": "OLED00", "sw_version": "4.10"}

    monkeypatch.setattr(pairing, "webos_pair", pair)
    text = ("HTTP/1.1 200 OK\r\nST: urn:lge-com:service:webos-second-screen:1\r\n"
            "USN: uuid:0c0b0a00-0000-0000-0000-0000000000bb\r\n\r\n")
    finding = parse_ssdp(text, "192.0.2.71")
    with pytest.raises(flows.Pending) as wait:
        await flows.adopt(rt, finding, {})
    await asyncio.sleep(0)
    result = await flows.adopt(rt, finding, {"input": {"session": wait.value.session}})
    entry = rt.hub.config_entries[result["entry_id"]]
    assert entry.data == {"host": "192.0.2.71", "client_secret": "k" * 32}
    assert entry.unique_id == "0c0b0a00-0000-0000-0000-0000000000bb"
    assert entry.title == "LG webOS TV OLED00"
    assert [e.unique_id for e in rt.hub.entities.values()] == [entry.unique_id]


# -- DLNA -------------------------------------------------------------------------------

DESC = """<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>
<deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType><friendlyName>Loft Speaker</friendlyName>
<manufacturer>Example Co</manufacturer><modelName>R1</modelName><UDN>uuid:00000000-0000-0000-0000-0000000000cc</UDN>
</device></root>"""


async def test_dlna_needs_no_input(rt, monkeypatch):  # noqa: F811
    async def describe(url):
        assert url == "http://192.0.2.72:9197/dmr"
        return DESC

    monkeypatch.setattr(pairing, "dlna_describe", describe)
    text = ("HTTP/1.1 200 OK\r\nST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
            "USN: uuid:00000000-0000-0000-0000-0000000000cc::urn:schemas-upnp-org:device:MediaRenderer:1\r\n"
            "LOCATION: http://192.0.2.72:9197/dmr\r\n\r\n")
    finding = parse_ssdp(text, "192.0.2.72")
    assert flows.needs_for(finding) == []
    result = await flows.adopt(rt, finding, {})
    entry = rt.hub.config_entries[result["entry_id"]]
    assert entry.data["url"] == "http://192.0.2.72:9197/dmr"
    assert entry.unique_id == "uuid:00000000-0000-0000-0000-0000000000cc"
    assert entry.title == "Loft Speaker"
    assert [e.unique_id for e in rt.hub.entities.values()] == [entry.unique_id]


# -- Reolink ----------------------------------------------------------------------------

class FakeApi:
    def __init__(self, nvr=True):
        self.uid = "95270000HUBUID00" if nvr else "Unknown"
        self.mac_address = "AA:BB:CC:00:00:90"
        self.is_nvr = nvr
        self.nvr_name = "Home Hub" if nvr else "Driveway"
        self.model = "Hub One" if nvr else "Cam One"
        self.sw_version = "v1"
        self.port, self.use_https = 443, True
        self.baichuan = type("B", (), {"port": 9000})()
        self.channels = [0, 1] if nvr else [0]
        self.logged_out = False
        self.caps = {0: {"motion_detection", "battery", "siren_play", "ir_lights", "dayNight", "ai_sensitivity"},
                     1: {"motion_detection", "status_led"}, None: {"state_light", "siren_play"}}

    def camera_uid(self, ch):
        return f"9527000CAM0000{ch}" if self.is_nvr else "Unknown"

    def camera_name(self, ch):
        return f"Camera {ch}"

    def camera_model(self, ch):
        return "Cam One"

    def supported(self, ch, cap):
        return cap in self.caps.get(ch, set())

    def ai_supported(self, ch, kind):
        return ch == 0 and kind == "people"

    async def logout(self):
        self.logged_out = True


def reolink_finding():
    return parse_mdns("_http._tcp.local.", "Reolink Hub._http._tcp.local.", "192.0.2.90", 80, {}, "reolink-hub.local.")


async def test_reolink_asks_for_the_login_and_creates_entities_by_capability(rt, monkeypatch):  # noqa: F811
    from reolink_aio.exceptions import CredentialsInvalidError

    api = FakeApi()

    async def connect(host, username, password):
        if password != "right":
            raise CredentialsInvalidError("bad login")
        return api

    monkeypatch.setattr(pairing, "reolink_connect", connect)
    finding = reolink_finding()
    with pytest.raises(flows.NeedsInput):
        await flows.adopt(rt, finding, {})
    with pytest.raises(flows.NeedsInput) as need:
        await flows.adopt(rt, finding, {"input": {"username": "admin", "password": "wrong"}})
    assert need.value.error == "That login was not accepted."
    result = await flows.adopt(rt, finding, {"input": {"username": "admin", "password": "right"}})
    assert api.logged_out
    hub = rt.hub
    entry = hub.config_entries[result["entry_id"]]
    assert entry.data["uid"] == "95270000HUBUID00" and entry.data["username"] == "admin"
    assert entry.unique_id == "aa:bb:cc:00:00:90"
    uids = {e.unique_id: e for e in hub.entities.values()}
    hub_uid, cam0, cam1 = "95270000HUBUID00", "9527000CAM00000", "9527000CAM00001"
    for key in ("sub", "main", "motion", "person", "battery_percent", "siren", "ir_lights", "day_night_mode",
                "ai_person_sensititvity"):
        assert f"{hub_uid}_{cam0}_{key}" in uids, key
    assert uids[f"{hub_uid}_{cam0}_main"].disabled_by == "integration"
    assert f"{hub_uid}_{cam0}_status_led" not in uids
    assert f"{hub_uid}_{cam1}_status_led" in uids and f"{hub_uid}_{cam1}_battery_percent" not in uids
    assert f"{hub_uid}_hub_status_led" in uids and f"{hub_uid}_siren" in uids
    cams = [d for d in hub.devices.values() if d.via_device_id]
    assert sorted(d.name for d in cams) == ["Camera 0", "Camera 1"]
    # the driver finds each entity's channel
    drv = ReolinkDriver(hub, entry)
    drv._channel_by_uid = {cam0: 0, cam1: 1}
    drv.host = None
    assert drv.locate(uids[f"{hub_uid}_{cam1}_motion"]) == (1, "motion")
    assert drv.locate(uids[f"{hub_uid}_hub_status_led"]) == (None, "status_led")


async def test_a_camera_without_uid_is_keyed_by_its_mac(rt, monkeypatch):  # noqa: F811
    async def connect(host, username, password):
        return FakeApi(nvr=False)

    monkeypatch.setattr(pairing, "reolink_connect", connect)
    result = await flows.adopt(rt, reolink_finding(), {"input": {"username": "admin", "password": "x"}})
    entry = rt.hub.config_entries[result["entry_id"]]
    assert "uid" not in entry.data
    uids = {e.unique_id for e in rt.hub.entities.values()}
    assert "aa:bb:cc:00:00:90_0_sub" in uids
    drv = ReolinkDriver(rt.hub, entry)
    drv.host = None
    assert drv.hub_uid == "aa:bb:cc:00:00:90"


# -- ESPHome builder and manual adds --------------------------------------------------

async def test_the_esphome_builder_is_an_entry_with_its_own_url(rt, monkeypatch):  # noqa: F811
    async def check(url, username, password):
        return {"status": 200 if username == "admin" and password == "pw" else 401,
                "login": not (username == "admin" and password == "pw")}

    monkeypatch.setattr(pairing, "builder_check", check)
    finding = pairing.manual_finding("esphome_builder", {"url": "http://192.0.2.5:6052/"})
    with pytest.raises(flows.NeedsInput) as need:
        await flows.adopt(rt, finding, {"input": {"url": "http://192.0.2.5:6052/"}})
    assert [n["key"] for n in need.value.needs] == ["username", "password"]
    result = await flows.adopt(rt, finding, {"input": {"url": "http://192.0.2.5:6052/", "username": "admin",
                                                       "password": "pw"}})
    entry = rt.hub.config_entries[result["entry_id"]]
    assert entry.domain == "esphome_builder" and entry.data["url"] == "http://192.0.2.5:6052/"
    with pytest.raises(flows.FlowError) as err:
        await flows.adopt(rt, finding, {"input": {"url": "http://192.0.2.6:6052/"}})
    assert err.value.status == 409


def test_manual_findings_check_what_was_typed():
    f = pairing.manual_finding("samsungtv", {"host": "192.0.2.70"})
    assert f["id"] == "manual:samsungtv:192.0.2.70" and f["host"] == "192.0.2.70"
    d = pairing.manual_finding("dlna_dmr", {"input": {"url": "http://192.0.2.72:9197/dmr"}})
    assert d["host"] == "192.0.2.72" and d["props"]["location"] == "http://192.0.2.72:9197/dmr"
    with pytest.raises(flows.FlowError):
        pairing.manual_finding("samsungtv", {"host": "bad host; rm"})
    with pytest.raises(flows.FlowError) as err:
        pairing.manual_finding("zigbee", {"host": "192.0.2.1"})
    assert err.value.status == 501


def test_an_encrypted_samsung_entry_gets_the_encrypted_bridge():
    from services.homehub.drivers.samsungtv import EncryptedBridge, SamsungTVDriver
    from services.homehub.tests.conftest import make_hub

    hub, entry = make_hub("samsungtv", {"host": "192.0.2.70", "method": "encrypted", "port": 8000, "token": "t",
                                        "session_id": "s"})
    drv = SamsungTVDriver(hub, entry)
    assert isinstance(drv.bridge, EncryptedBridge)
    assert drv.bridge.port == 8000 and drv.bridge.power_key == "KEY_POWEROFF"


async def test_the_builder_entry_loads_while_the_dashboard_answers():
    from services.homehub.core import DeviceUnavailable
    from services.homehub.drivers.esphome_builder import ESPHomeBuilderDriver
    from services.homehub.tests.conftest import make_hub

    hub, entry = make_hub("esphome_builder", {"url": "http://192.0.2.5:6052/"})
    drv = ESPHomeBuilderDriver(hub, entry)
    answers = [{"status": 200, "login": False}]

    async def check(url, username=None, password=None):
        if not answers:
            raise DeviceUnavailable("down")
        return answers.pop()

    drv.check = check
    await drv.async_setup()
    await drv.async_poll()
    assert entry.state == "setup_retry" and "down" in entry.reason
