"""A hub that never imported anything: new devices found, added and driven with only Jarvis's own data.

The store starts empty (no Home Assistant import at all, and the importer is made to fail if anything calls
it). Devices are found by discovery from fake protocol replies, added through their flows, and driven by the
real drivers through fake transports. Everything the hub then knows about them (maker, model, firmware, MAC,
feature flags, colour modes, ranges, units) must have come from the fake devices, and be marked as theirs.

Covered: WiZ (UDP sweep), ESPHome (mDNS), Google Cast (mDNS), Roku (SSDP), HomeKit (mDNS + pairing), an MJPEG
camera (added by hand, served by a local HTTP server) and an Emporia cloud account. Every name, address, MAC,
serial and key here is made up.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, ClassVar
from uuid import UUID

import httpx
import pytest
from aioesphomeapi import (
    APIVersion,
    ColorMode,
    DeviceInfo,
    LightInfo,
    LightState,
    SensorInfo,
    SensorState,
    SensorStateClass,
)
from aiohttp import web
from cryptography.fernet import Fernet

from services.homehub import capabilities, discovery, importer, manual_flows, pairing
from services.homehub import flows as flows_mod
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.automation_editor import catalog
from services.homehub.core import Hub
from services.homehub.discovery import DiscoveryManager
from services.homehub.drivers import cast as cast_mod
from services.homehub.drivers import esphome as esphome_mod
from services.homehub.drivers import roku as roku_mod
from services.homehub.drivers import wiz as wiz_mod
from services.homehub.drivers.cast import CastDriver
from services.homehub.drivers.emporia import EmporiaDriver
from services.homehub.drivers.esphome import ESPHomeDriver
from services.homehub.drivers.homekit import HomeKitDriver
from services.homehub.drivers.roku import RokuDriver
from services.homehub.runtime import Runtime
from services.homehub.store import Store
from services.homehub.tests.media_fakes import FakeHttp
from services.homehub.tests.test_homekit_services import BRIDGE, FakePairing

SECRET = "fresh-hub-secret"
H = {"Authorization": f"Bearer {SECRET}"}

WIZ_HOST, WIZ_MAC = "192.0.2.21", "02000000aa21"
ESP_HOST, ESP_MAC = "192.0.2.41", "02:00:00:00:00:41"
CAST_HOST, CAST_UUID = "192.0.2.51", "00000000-0000-4000-8000-0000000000c5"
ROKU_HOST, ROKU_NEW_HOST, ROKU_SERIAL = "192.0.2.31", "192.0.2.131", "X00000000031"
HK_HOST, HK_ID = "192.0.2.61", "02:00:00:00:00:61"
EMPORIA_GID = 700001


# -- fake devices ------------------------------------------------------------------------------

class FakeBulb:
    """A WiZ RGB bulb answering the UDP methods the flow and the driver use."""

    def __init__(self) -> None:
        self.pilot: dict[str, Any] = {"state": True, "dimming": 60, "temp": 3000, "sceneId": 0}
        self.sets: list[dict[str, Any]] = []

    async def request(self, host: str, method: str, params: dict[str, Any] | None = None, **_kw: Any) -> dict:
        assert host == WIZ_HOST
        if method == "getSystemConfig":
            return {"mac": WIZ_MAC, "moduleName": "ESP25_SHRGB_01", "fwVersion": "1.99.0"}
        if method == "getModelConfig":
            return {"cctRange": [2100, 2700, 6000, 6000]}
        if method == "getPilot":
            return dict(self.pilot)
        if method == "getPower":
            return {"power": 7300}
        if method == "setPilot":
            self.sets.append(dict(params or {}))
            self.pilot.update(params or {})
            return {"success": True}
        raise AssertionError(method)


ESP_INFOS = [
    LightInfo(object_id="lamp", key=1, name="", supported_color_modes=[ColorMode.RGB], min_mireds=0, max_mireds=0,
              effects=["None", "Rainbow"]),
    SensorInfo(object_id="temperature", key=2, name="Temperature", unit_of_measurement="°C",
               device_class="temperature", state_class=SensorStateClass.MEASUREMENT, accuracy_decimals=1),
]


class FakeNode:
    """An ESPHome node over the native API."""

    instances: ClassVar[list[FakeNode]] = []

    def __init__(self) -> None:
        self.api_version = APIVersion(1, 12)
        self.commands: list[tuple[str, tuple, dict]] = []
        FakeNode.instances.append(self)

    async def connect(self, on_stop=None, login=False):
        self.on_stop = on_stop

    async def device_info_and_list_entities(self):
        info = DeviceInfo(name="desk-node", friendly_name="Desk Node", mac_address=ESP_MAC, esphome_version="2026.9.0",
                          manufacturer="Example Boards", model="esp32-c6", project_name="example.desk_lamp",
                          project_version="2.1", webserver_port=80)
        return info, list(ESP_INFOS), []

    def subscribe_home_assistant_states_and_services(self, on_state, on_service_call, on_state_sub,
                                                     on_state_request=None):
        self.on_state = on_state
        on_state(LightState(key=1, state=True, brightness=0.8, color_mode=ColorMode.RGB, red=1.0, green=0.5,
                            blue=0.0, effect="None"))
        on_state(SensorState(key=2, state=21.5))

    async def disconnect(self, force=False):
        pass

    def __getattr__(self, name):
        if not name.endswith("_command"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.commands.append((name, args, kwargs))
        return record


class FakeChromecast:
    """A Cast speaker: its Cast info, status and the commands it was sent."""

    def __init__(self, info: Any) -> None:
        self.info = info
        self.status = SimpleNamespace(volume_level=0.3, volume_muted=False, app_id=None, display_name=None,
                                      volume_control_type="attenuation")
        self.media_controller = SimpleNamespace(status=None, register_status_listener=lambda listener: None)
        self.calls: list[tuple] = []

    is_idle = True
    app_id = None

    def register_status_listener(self, listener):
        pass

    def register_connection_listener(self, listener):
        pass

    def start(self):
        pass

    def set_volume(self, level):
        self.calls.append(("set_volume", level))
        self.status.volume_level = level

    def __getattr__(self, name):
        def rec(*a, **kw):
            self.calls.append((name, *a))
        return rec


class FakeBrowser:
    """pychromecast's CastBrowser: it 'finds' the one speaker on the LAN."""

    def __init__(self, listener, zconf, hosts):
        self.listener = listener
        self.devices: dict[UUID, Any] = {}

    def start_discovery(self):
        u = UUID(CAST_UUID)
        self.devices[u] = SimpleNamespace(uuid=u, friendly_name="Kitchen speaker", host=CAST_HOST, port=8009,
                                          manufacturer="Example Audio", model_name="Example Speaker Mini")
        self.listener.add_cast(u, "svc")

    def stop_discovery(self):
        pass


ROKU_INFO = {"serial-number": ROKU_SERIAL, "user-device-name": "Den TV", "vendor-name": "Example TV Co",
             "model-name": "Example Roku TV 55", "software-version": "14.1.4", "wifi-mac": "02:00:00:00:00:31",
             "is-tv": "true", "power-mode": "PowerOn"}
ROKU_XML = "<device-info>" + "".join(f"<{k}>{v}</{k}>" for k, v in ROKU_INFO.items()) + "</device-info>"


def roku_http() -> FakeHttp:
    http = FakeHttp()
    http.route("GET", "/query/device-info", (200, ROKU_XML))
    http.route("GET", "/query/apps", (200, '<apps><app id="12" type="appl">Example Movies</app></apps>'))
    http.route("GET", "/query/active-app", (200, '<active-app><app id="12" type="appl">Example Movies</app>'
                                                 "</active-app>"))
    http.route("GET", "/query/media-player", (200, '<player state="play"><position>1000 ms</position></player>'))
    http.route("POST", ":8060/", (200, ""))
    return http


class FakeVue:
    """Emporia's cloud for one account with one monitor."""

    def login(self, email, password):
        return email == "someone@example.com" and password == "s3cret"

    def get_devices(self):
        channels = [SimpleNamespace(channel_num=n, name=None if n == "1,2,3" else f"Circuit {n}", type="Main")
                    for n in ("1,2,3", "4")]
        return [SimpleNamespace(device_gid=EMPORIA_GID, device_name="Main panel", model="VUE003",
                                firmware="Vue2-12", channels=channels, time_zone="Etc/GMT+7",
                                billing_cycle_start_day=1)]

    def get_device_list_usage(self, gids, instant, scale, unit, retries):
        usage = {"1MIN": {"1,2,3": 0.05, "4": 0.01}, "1D": {"1,2,3": 20.0, "4": 2.0},
                 "1MON": {"1,2,3": 400.0, "4": 40.0}}[scale]
        chans = {n: SimpleNamespace(device_gid=EMPORIA_GID, channel_num=n, usage=u, nested_devices={})
                 for n, u in usage.items()}
        return {EMPORIA_GID: SimpleNamespace(channels=chans)}


JPEG = b"\xff\xd8\xff\xe0" + b"fresh-hub-frame" + b"\xff\xd9"


async def camera_server() -> web.AppRunner:
    async def still(_request):
        return web.Response(body=JPEG, content_type="image/jpeg")

    async def stream(_request):
        return web.Response(body=b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + JPEG + b"\r\n",
                            content_type="multipart/x-mixed-replace", charset=None)

    app = web.Application()
    app.router.add_get("/still.jpg", still)
    app.router.add_get("/video.mjpg", stream)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner


# -- the fresh hub -----------------------------------------------------------------------------

async def until(check, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not check():
        if loop.time() > end:
            raise AssertionError("timed out waiting for the hub")
        await asyncio.sleep(0.02)


@pytest.fixture
async def fresh(tmp_path, monkeypatch):
    def no_import(*_a, **_kw):
        raise AssertionError("a fresh hub must never need Home Assistant's data")

    monkeypatch.setattr(importer, "import_config_dir", no_import)
    monkeypatch.setattr(importer, "import_history", no_import)
    bulb = FakeBulb()
    monkeypatch.setattr(wiz_mod, "udp_request", bulb.request)
    monkeypatch.setattr(ESPHomeDriver, "_make_client", lambda self, host: FakeNode())
    monkeypatch.setattr(esphome_mod, "COMMAND_SETTLE", 0.01)

    async def esphome_probe(host, port, psk, password=""):
        node = FakeNode()
        info, infos, _ = await node.device_info_and_list_entities()
        return info, infos

    monkeypatch.setattr(flows_mod, "esphome_probe", esphome_probe)
    monkeypatch.setattr(cast_mod, "CastBrowser", FakeBrowser)
    casts: list[FakeChromecast] = []

    def make_cast(info, zconf):
        casts.append(FakeChromecast(info))
        return casts[-1]

    monkeypatch.setattr(CastDriver, "cast_factory", staticmethod(make_cast))
    monkeypatch.setattr(CastDriver, "zeroconf_factory", staticmethod(lambda: object()))
    http = roku_http()
    monkeypatch.setattr(roku_mod, "Http", lambda timeout=None: http)
    monkeypatch.setattr(RokuDriver, "settle_delay", 0)

    async def roku_device_info(host):
        assert host in (ROKU_HOST, ROKU_NEW_HOST)
        return dict(ROKU_INFO)

    monkeypatch.setattr(flows_mod, "roku_device_info", roku_device_info)

    async def homekit_pair(finding, code, alias):
        assert code == "123-45-678"
        return ({"AccessoryPairingID": HK_ID, "AccessoryIP": HK_HOST, "AccessoryPort": 51826, "Connection": "IP",
                 "iOSPairingId": "made-up-controller", "iOSDeviceLTSK": "00", "AccessoryLTPK": "00"},
                copy.deepcopy(BRIDGE))

    monkeypatch.setattr(pairing, "homekit_pair", homekit_pair)
    accessory = FakePairing(BRIDGE)

    async def make_pairing(self):
        return accessory

    monkeypatch.setattr(HomeKitDriver, "_make_pairing", make_pairing)
    vue = FakeVue()

    async def emporia_login(email, password):
        if not vue.login(email, password):
            raise flows_mod.NeedsInput([manual_flows.EMAIL, manual_flows.PASSWORD], "Sign in to Emporia.")
        return vue.get_devices()

    monkeypatch.setattr(manual_flows, "emporia_login", emporia_login)
    monkeypatch.setattr(EmporiaDriver, "_new_client", lambda self: vue)
    monkeypatch.setattr(discovery, "MIN_SCAN_GAP_S", 0)

    store = Store(str(tmp_path / "fresh.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="Etc/GMT+7")
    hub.store = store
    runtime = Runtime(hub, store)
    runtime.load_registry()
    runtime.start_recording()
    mgr = DiscoveryManager(hub, store, notifier=lambda payload: asyncio.sleep(0, True))
    await mgr.start(mdns=False, periodic=False)
    runtime.discovery = mgr
    ssdp_hosts = {"host": ROKU_HOST}

    async def ssdp_search(hosts):
        return [("HTTP/1.1 200 OK\r\nST: roku:ecp\r\nUSN: uuid:roku:ecp:" + ROKU_SERIAL + "\r\n"
                 f"LOCATION: http://{ssdp_hosts['host']}:8060/\r\nSERVER: Roku/14.1\r\n", ssdp_hosts["host"])]

    async def wiz_sweep(hosts):
        return [({"method": "getSystemConfig", "result": await bulb.request(WIZ_HOST, "getSystemConfig")}, WIZ_HOST)]

    async def no_description(url):
        return None

    mgr.ssdp_search = ssdp_search
    mgr.wiz_sweep = wiz_sweep
    mgr.fetch_description = no_description
    mgr.read_arp = lambda: ""
    mgr.subnet_hosts = lambda: [WIZ_HOST, ROKU_HOST]
    cam = await camera_server()
    port = cam.addresses[0][1]
    app = build_app(runtime, Auth(store, SECRET))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://hub.example.com")
    env = SimpleNamespace(hub=hub, store=store, runtime=runtime, mgr=mgr, client=client, bulb=bulb, casts=casts,
                          roku_http=http, accessory=accessory, vue=vue, ssdp_hosts=ssdp_hosts,
                          camera_base=f"http://127.0.0.1:{port}")
    try:
        yield env
    finally:
        await client.aclose()
        await mgr.stop()
        await runtime.shutdown()
        await cam.cleanup()
        store.close()


def finding_id(env, integration: str) -> str:
    return next(f["id"] for f in env.mgr.list() if f["integration"] == integration)


async def adopt(env, integration: str, body: dict[str, Any]) -> dict[str, Any]:
    r = await env.client.post(f"/hub/discovered/{finding_id(env, integration)}/adopt", json=body, headers=H)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "adopted", out
    return out


def device_of(env, result) -> Any:
    return env.hub.devices[result["device_id"]]


def entity(env, result, domain: str) -> str:
    return next(e for e in result["entities"] if e.startswith(domain + "."))


async def test_a_fresh_hub_finds_adds_and_drives_new_devices_with_no_home_assistant_data(fresh):
    env = fresh
    hub, store, client = env.hub, env.store, env.client
    assert store.count("config_entries") == store.count("devices") == store.count("entities") == 0
    assert store.get_kv("imported_at") is None and hub.areas == {}

    # A person makes the rooms (a fresh hub has none).
    for name in ("Kitchen", "Den", "Office", "Porch"):
        r = await client.post("/hub/areas", json={"name": name}, headers=H)
        assert r.status_code == 200, r.text
    assert sorted(hub.areas) == ["den", "kitchen", "office", "porch"]

    # -- discovery from the protocols ----------------------------------------------------
    await env.mgr.scan(reason="on demand")  # SSDP (Roku) and the WiZ sweep
    env.mgr.handle_mdns("_esphomelib._tcp.local.", "desk-node._esphomelib._tcp.local.", ESP_HOST, 6053,
                        {b"friendly_name": b"Desk Node", b"mac": ESP_MAC.replace(":", "").encode(),
                         b"platform": b"ESP32"}, "desk-node.local.")
    env.mgr.handle_mdns("_googlecast._tcp.local.", "Kitchen-speaker._googlecast._tcp.local.", CAST_HOST, 8009,
                        {b"id": UUID(CAST_UUID).hex.encode(), b"fn": b"Kitchen speaker",
                         b"md": b"Example Speaker Mini"}, "kitchen-speaker.local.")
    env.mgr.handle_mdns("_hap._tcp.local.", "Porch Bridge._hap._tcp.local.", HK_HOST, 51826,
                        {b"sf": b"1", b"id": HK_ID.encode(), b"ff": b"1", b"ci": b"2", b"c#": b"4",
                         b"md": b"Bridge"}, "porch-bridge.local.")
    found = (await client.get("/hub/discovered?new=1", headers=H)).json()
    found = found if isinstance(found, list) else found.get("devices") or found.get("findings") or found
    integrations = sorted(f["integration"] for f in (found if isinstance(found, list) else []))
    assert integrations == ["cast", "esphome", "homekit_controller", "roku", "wiz"]

    # -- the flows, through the admin API ------------------------------------------------
    wiz = await adopt(env, "wiz", {"name": "Kitchen lamp", "area_id": "Kitchen"})
    esp = await adopt(env, "esphome", {"name": "Desk lamp", "area_id": "Office"})
    cast = await adopt(env, "cast", {"area_id": "Kitchen"})
    roku = await adopt(env, "roku", {"area_id": "Den"})
    r = await client.post(f"/hub/discovered/{finding_id(env, 'homekit_controller')}/adopt",
                          json={"area_id": "Porch", "input": {"code": "12345678"}}, headers=H)
    hk = r.json()
    assert hk["status"] == "adopted", hk
    r = await client.post("/hub/flows/mjpeg", json={"name": "Porch camera", "area_id": "Porch", "input": {
        "mjpeg_url": env.camera_base + "/video.mjpg", "still_image_url": env.camera_base + "/still.jpg"}}, headers=H)
    cam = r.json()
    assert cam["status"] == "adopted", cam
    r = await client.post("/hub/flows/emporia_vue", json={"input": {"email": "someone@example.com",
                                                                    "password": "s3cret"}}, headers=H)
    emp = r.json()
    assert emp["status"] == "adopted", emp
    assert await env.runtime.wait_first_setups(10) == []
    for entry_id in (wiz["entry_id"], esp["entry_id"], roku["entry_id"], hk["entry_id"], cam["entry_id"],
                     emp["entry_id"]):
        assert hub.config_entries[entry_id].state == "loaded", hub.config_entries[entry_id].reason
    cast_player = entity(env, cast, "media_player")
    await until(lambda: (st := hub.states.get(cast_player)) is not None and st.state != "unavailable")

    # -- device information: the devices' own, and marked as theirs -----------------------
    lamp_dev = device_of(env, wiz)
    assert (lamp_dev.manufacturer, lamp_dev.model, lamp_dev.sw_version) == ("WiZ", "ESP25_SHRGB_01", "1.99.0")
    assert lamp_dev.connections == [["mac", "02:00:00:00:aa:21"]] and lamp_dev.area_id == "kitchen"
    node_dev = device_of(env, esp)
    assert (node_dev.manufacturer, node_dev.model) == ("Example Boards", "example.desk_lamp")
    assert node_dev.sw_version == "2.1 (ESPHome 2026.9.0)" and ["mac", ESP_MAC] in node_dev.connections
    speaker_dev = device_of(env, cast)
    assert (speaker_dev.manufacturer, speaker_dev.model) == ("Example Audio", "Example Speaker Mini")
    tv_dev = device_of(env, roku)
    assert (tv_dev.manufacturer, tv_dev.model, tv_dev.serial_number, tv_dev.sw_version) == (
        "Example TV Co", "Example Roku TV 55", ROKU_SERIAL, "14.1.4")
    hk_devs = [d for d in hub.devices.values() if hk["entry_id"] in d.config_entries]
    assert len(hk_devs) == len(BRIDGE) and all(d.manufacturer for d in hk_devs)
    panel = device_of(env, emp)
    assert (panel.manufacturer, panel.model, panel.sw_version) == ("Emporia", "VUE003", "Vue2-12")
    for dev in [lamp_dev, node_dev, speaker_dev, tv_dev, *hk_devs, panel]:
        assert dev.info_source() and set(dev.info_source().values()) == {"device"}, (dev.name, dev.info_source())
    for ent in hub.entities.values():
        assert set(ent.info_source().values()) <= {"device"}, (ent.entity_id, ent.info_source())

    # -- capabilities from the devices ---------------------------------------------------
    lamp = entity(env, wiz, "light")
    cap = capabilities.entity_capabilities(hub, lamp)
    assert cap["confirmed"] and cap["source"] == "device" and cap["imported_only"] == []
    assert {"power", "dim", "color", "color_temp", "effect"} <= set(cap["abilities"])
    assert cap["details"]["min_color_temp_kelvin"] == 2100 and cap["details"]["max_color_temp_kelvin"] == 6000
    desk = entity(env, esp, "light")
    assert {"dim", "color", "effect"} <= set(capabilities.abilities_of(hub, desk))
    temp = next(e for e in esp["entities"] if e.startswith("sensor."))
    assert hub.states.get(temp).attributes["unit_of_measurement"] == "°C"
    assert hub.states.get(temp).attributes["state_class"] == "measurement"
    assert {"cast", "volume", "play_media"} <= set(capabilities.abilities_of(hub, cast_player))
    tv = entity(env, roku, "media_player")
    tv_cap = capabilities.entity_capabilities(hub, tv)
    assert {"launch_app", "select_source", "pause"} <= set(tv_cap["abilities"])
    assert tv_cap["details"]["device_class"] == "tv" and "Example Movies" in tv_cap["details"]["sources"]
    porch_light = next(e.entity_id for e in hub.entities.values()
                       if e.config_entry_id == hk["entry_id"] and e.domain == "light")
    assert "dim" in capabilities.abilities_of(hub, porch_light)
    camera = entity(env, cam, "camera")
    assert {"camera_snapshot", "camera_stream"} <= set(capabilities.abilities_of(hub, camera))
    power = next(e for e in emp["entities"] if "instant" in hub.entities[e].unique_id
                 and hub.entities[e].unique_id.endswith("-1,2,3"))
    assert hub.states.get(power).attributes["unit_of_measurement"] == "W"
    assert hub.states.get(power).attributes["device_class"] == "power"
    assert capabilities.provenance(hub)["entities_imported_only"] == 0

    # The index over HTTP: by ability and by room, with the areas.
    body = (await client.get("/api/homehub/capabilities", params={"ability": "dim", "area": "Kitchen"},
                             headers=H)).json()
    assert [e["entity_id"] for e in body["entities"]] == [lamp]
    assert {a["name"] for a in body["areas"]} == {"Kitchen", "Den", "Office", "Porch"}
    body = (await client.get("/api/homehub/capabilities", params={"ability": "cast"}, headers=H)).json()
    assert [e["entity_id"] for e in body["entities"]] == [cast_player]

    # -- the pages: hardware list, rooms, automation editor ------------------------------
    hw = (await client.get("/hub/hardware", headers=H)).json()["devices"]
    row = next(d for d in hw if d["id"] == lamp_dev.id)
    assert row["model"] == "ESP25_SHRGB_01" and row["status"] == "online" and "dim" in row["abilities"]
    assert row["info_source"]["model"] == "device"
    assert {"type": "ip", "value": WIZ_HOST} in row["connections"]
    detail = (await client.get(f"/hub/hardware/{tv_dev.id}", headers=H)).json()
    assert any(e["entity_id"] == tv and "launch_app" in e["capabilities"]["abilities"] for e in detail["entity_list"])
    rooms = (await client.get("/api/homehub/dashboard/rooms", headers=H)).json()
    kitchen = next(a for a in rooms["areas"] if a["area_id"] == "kitchen")
    by_id = {e["entity_id"]: e for e in kitchen["entities"]}
    assert "dim" in by_id[lamp]["abilities"] and "volume" in by_id[cast_player]["abilities"]
    editor = {e["entity_id"]: e for e in catalog(hub)["entities"]}
    assert "light.turn_on" in editor[lamp]["services"] and "media_player.volume_set" in editor[cast_player]["services"]
    assert "media_player.select_source" in editor[tv]["services"]

    # -- services reach the devices ------------------------------------------------------
    r = await client.post("/api/services/light/turn_on", json={"entity_id": lamp, "brightness": 255,
                                                               "rgb_color": [0, 0, 255]}, headers=H)
    assert r.status_code == 200, r.text
    assert env.bulb.sets[-1]["state"] is True and env.bulb.sets[-1]["b"] == 255
    await hub.call_service("light", "turn_off", {"entity_id": desk})
    node = FakeNode.instances[-1]
    assert node.commands and node.commands[-1][0] == "light_command"
    await hub.call_service("media_player", "volume_set", {"entity_id": cast_player, "volume_level": 0.6})
    assert ("set_volume", 0.6) in env.casts[-1].calls
    await hub.call_service("media_player", "media_pause", {"entity_id": tv})
    assert env.roku_http.posted("keypress/")
    await hub.call_service("light", "turn_off", {"entity_id": porch_light})
    assert env.accessory.puts
    r = await client.get(f"/api/camera_proxy/{camera}", headers=H)
    assert r.status_code == 200 and r.content == JPEG

    # -- history starts recording ----------------------------------------------------------
    rows = store.history([lamp, tv, power], datetime.now(UTC) - timedelta(minutes=5))
    assert {r["entity_id"] for r in rows} == {lamp, tv, power}

    # -- a device that moves is offered as moved --------------------------------------------
    env.ssdp_hosts["host"] = ROKU_NEW_HOST
    await env.mgr.scan(reason="on demand")
    moved = next(f for f in env.mgr.list() if f["integration"] == "roku")
    assert moved["known"] and moved["moved"] == {"entry_id": roku["entry_id"], "entry_title": "Den TV",
                                                 "old_host": ROKU_HOST, "new_host": ROKU_NEW_HOST}
    r = await client.post(f"/hub/discovered/{moved['id']}/update-address", headers=H)
    assert r.status_code == 200 and r.json()["status"] == "updated", r.text
    assert hub.config_entries[roku["entry_id"]].data["host"] == ROKU_NEW_HOST
    await until(lambda: any(ROKU_NEW_HOST in c["url"] for c in env.roku_http.calls))

    # -- and a restart reads it all back from the hub's own store --------------------------
    again = Hub(time_zone="Etc/GMT+7")
    Runtime(again, store).load_registry()
    assert again.devices[lamp_dev.id].model == "ESP25_SHRGB_01"
    assert again.entities[lamp].state_confirmed and again.areas.keys() == hub.areas.keys()
    assert capabilities.entity_capabilities(again, lamp)["confirmed"]
