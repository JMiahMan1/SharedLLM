"""Real-time video through go2rtc: stream registration, settings, signaling, and that no password escapes.

go2rtc is faked at its HTTP API (``Go2rtcClient.request``); the Reolink hub is
the fake host from the camera driver tests, with stream URLs that carry a
made-up password so every test can look for it where it must never be.
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest
import yaml
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import live_video, live_video_api
from services.homehub.api import build_app
from services.homehub.auth import Auth, Principal
from services.homehub.core import ConfigEntry, DeviceEntry, EntityEntry
from services.homehub.drivers import reolink
from services.homehub.runtime import Runtime
from services.homehub.store import Store
from services.homehub.tests.test_drivers_cameras import FakeHost, reolink_hub

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}
MEMBER = {"Authorization": "Bearer member-token"}
#: Made-up camera credentials; none of them may ever reach a log line or an answer.
CAM_PASSWORD = "Zq9-camera-pass"
MJPEG_PASSWORD = "mjpeg-Secret-77"
GO2RTC_URL = "http://go2rtc.test:1984"
REO = "camera.garage_camera_1_fluent"


class StreamHost(FakeHost):
    """The fake Reolink hub, answering stream sources the way the Home Hub does (RTSP, password in the URL)."""

    async def get_stream_source(self, ch, stream=None, check=True):
        assert check is False  # checking opens the stream, which would wake a battery camera
        return f"rtsp://admin:{CAM_PASSWORD}@hub.test:554/Preview_{ch + 1:02d}_{stream}"


class FakeGo2rtc:
    """go2rtc's API as far as the hub uses it, in memory."""

    def __init__(self):
        self.streams: dict[str, str] = {"door_intercom": "rtsp://elsewhere.test/x"}  # not the hub's
        self.preload: set[str] = set()
        self.config: dict = {}
        self.calls: list[tuple[str, str, dict]] = []
        self.restarts = 0
        self.refuse = False
        self.down = False

    async def request(self, method, path, *, params=None, data=None, headers=None, timeout=None):
        params = dict(params or {})
        self.calls.append((method, path, params))
        if self.down:
            raise live_video.Go2rtcError("go2rtc did not answer (ClientConnectorError)")
        if path == "/api":
            return 200, b'{"version": "1.9.15", "config_path": "/config/go2rtc.yaml"}', "application/json"
        if path == "/api/streams":
            if method == "GET":
                return 200, json.dumps({n: {"producers": [{"url": "rtsp://***@hub"}]} for n in self.streams}).encode(), \
                    "application/json"
            if method == "PATCH":
                if self.refuse:
                    # go2rtc's own words can quote the source back.
                    return 400, f"streams: wrong source {params['src']}".encode(), "text/plain"
                self.streams[params["name"]] = params["src"]
                return 200, b"", ""
            if method == "DELETE":
                self.streams.pop(params["src"], None)
                return 200, b"", ""
            return 405, b"", ""  # PUT would write the source to the config file: never used
        if path == "/api/preload":
            if method == "GET":
                return 200, json.dumps({n: "" for n in self.preload}).encode(), "application/json"
            (self.preload.add if method == "PUT" else self.preload.discard)(params["src"])
            return 200, b"", ""
        if path == "/api/config":
            if method == "GET":
                return 200, yaml.safe_dump(self.config).encode(), "application/yaml"
            for k, v in json.loads(data).items():
                self.config[k] = {**self.config.get(k, {}), **v}
            return 200, b"", ""
        if path == "/api/restart":
            self.restarts += 1
            self.streams.clear()  # a restart forgets what was registered through the API
            return 200, b"", ""
        if path == "/api/webrtc":
            assert params["src"] in self.streams
            if headers.get("Content-Type") == "application/sdp":
                return 201, b"v=0\r\nanswer\r\n", "application/sdp"
            return 200, json.dumps({"type": "answer", "sdp": "v=0\r\nanswer\r\n"}).encode(), "application/json"
        return 404, b"", ""


class RelayCam:
    """An MJPEG camera: an http URL with its basic-auth password in it."""

    def __init__(self, hub, entry):
        self.hub, self.entry = hub, entry

    async def async_camera_image(self, ent):
        return b"\xff\xd8jpeg\xff\xd9"

    async def async_camera_stream_url(self, ent):
        return f"http://viewer:{MJPEG_PASSWORD}@printer.test/video.mjpg"


class EspCam(RelayCam):
    async def async_camera_stream_url(self, ent):
        return None  # ESPHome: pictures over its native API, no stream URL


@pytest.fixture
def fake_go2rtc(monkeypatch):
    fake = FakeGo2rtc()

    async def request(_self, method, path, **kw):
        return await fake.request(method, path, **kw)

    monkeypatch.setattr(live_video.Go2rtcClient, "request", request)
    return fake


def _add_camera(hub, eid, platform, cls):
    e = ConfigEntry(f"e_{platform}", platform, platform)
    hub.config_entries[e.entry_id] = e
    hub.devices[f"d_{platform}"] = DeviceEntry(id=f"d_{platform}", name=platform, config_entries=[e.entry_id])
    hub.entities[eid] = EntityEntry(entity_id=eid, platform=platform, unique_id=eid, config_entry_id=e.entry_id,
                                    device_id=f"d_{platform}", original_name="Camera")
    hub.drivers[e.entry_id] = cls(hub, e)
    hub.publish(eid, "idle", {})


async def _hub_with_go2rtc(monkeypatch, options=None, data=None):
    FakeHost.instances = []
    monkeypatch.setattr(reolink, "Host", StreamHost)
    hub, entry = reolink_hub()
    drv = reolink.ReolinkDriver(hub, entry)
    await drv.async_setup()
    hub.drivers[entry.entry_id] = drv
    _add_camera(hub, "camera.printer", "mjpeg", RelayCam)
    _add_camera(hub, "camera.garage_door_camera", "esphome", EspCam)
    g_entry = ConfigEntry("e_go2rtc", "go2rtc", "go2rtc", data=data or {"url": GO2RTC_URL}, options=options or {})
    hub.config_entries[g_entry.entry_id] = g_entry
    g = live_video.Go2rtcDriver(hub, g_entry)
    await g.async_setup()
    hub.drivers[g_entry.entry_id] = g
    return hub, g


# -- registration ---------------------------------------------------------------------

async def test_setup_registers_every_camera_in_memory_only(monkeypatch, fake_go2rtc):
    _hub, g = await _hub_with_go2rtc(monkeypatch)
    assert fake_go2rtc.streams[f"{REO}:sub"].endswith("/Preview_01_sub")
    assert fake_go2rtc.streams[f"{REO}:main"].endswith("/Preview_01_main")
    # PATCH keeps a stream in go2rtc's memory; PUT would write its password into go2rtc.yaml.
    assert not [c for c in fake_go2rtc.calls if c[0] == "PUT" and c[1] == "/api/streams"]
    # An MJPEG camera stays on the hub's own relay unless transcoding is asked for; ESPHome has no URL.
    assert not any(n.startswith(("camera.printer", "camera.garage_door_camera")) for n in fake_go2rtc.streams)
    assert "door_intercom" in fake_go2rtc.streams  # go2rtc's own streams are left alone
    assert g.describe(REO) == {"modes": ["webrtc", "mse", "mp4"], "qualities": ["sub", "main"],
                               "default_quality": "sub", "on_demand": True}
    assert g.describe("camera.printer") is None
    assert "MJPEG" in g.status()["cameras"]["camera.printer"]["note"]


async def test_sync_is_idle_when_nothing_changed_and_heals_a_restarted_go2rtc(monkeypatch, fake_go2rtc):
    _hub, g = await _hub_with_go2rtc(monkeypatch)
    patches = lambda: [c for c in fake_go2rtc.calls if c[0] == "PATCH" and c[1] == "/api/streams"]  # noqa: E731
    before = len(patches())
    await g.sync()
    assert len(patches()) == before
    fake_go2rtc.streams.clear()  # go2rtc restarted on its own
    await g.sync()
    assert len(patches()) == before + 2 and f"{REO}:main" in fake_go2rtc.streams


async def test_a_removed_or_turned_off_camera_leaves_go2rtc(monkeypatch, fake_go2rtc):
    hub, g = await _hub_with_go2rtc(monkeypatch)
    g.entry.options = {"cameras": {REO: {"disabled": True}}}
    await g.sync()
    assert not any(n.startswith(REO) for n in fake_go2rtc.streams)
    assert g.describe(REO) is None
    g.entry.options = {}
    await g.sync()
    assert f"{REO}:sub" in fake_go2rtc.streams
    del hub.entities[REO]
    await g.sync()
    assert not any(n.startswith(REO) for n in fake_go2rtc.streams) and REO not in g.cameras
    assert "door_intercom" in fake_go2rtc.streams


async def test_transcoding_an_mjpeg_camera_is_opt_in(monkeypatch, fake_go2rtc):
    _hub, g = await _hub_with_go2rtc(monkeypatch, options={"cameras": {"camera.printer": {"transcode": True}}})
    src = fake_go2rtc.streams["camera.printer:main"]
    assert src.startswith("ffmpeg:http://") and src.endswith("#video=h264")
    assert g.describe("camera.printer")["qualities"] == ["main"]


async def test_quality_and_on_demand_come_from_the_options(monkeypatch, fake_go2rtc):
    _hub, g = await _hub_with_go2rtc(monkeypatch, options={
        "default_quality": "main", "cameras": {"camera.printer": {"transcode": True, "on_demand": False}}})
    assert g.describe(REO)["default_quality"] == "sub"  # the entity is the camera's sub ("fluent") stream
    g.entry.options = {"cameras": {REO: {"quality": "main", "on_demand": False}}}
    await g.sync()
    # A battery camera is never held open: it could not sleep.
    assert g.describe(REO) == {"modes": ["webrtc", "mse", "mp4"], "qualities": ["sub", "main"],
                               "default_quality": "main", "on_demand": True}
    assert not fake_go2rtc.preload
    assert "battery" in g.status()["cameras"][REO]["note"]


async def test_a_mains_camera_can_be_kept_open(monkeypatch, fake_go2rtc):
    _hub, g = await _hub_with_go2rtc(monkeypatch, options={
        "cameras": {"camera.printer": {"transcode": True, "on_demand": False}}})
    assert fake_go2rtc.preload == {"camera.printer:main"}
    g.entry.options = {"cameras": {"camera.printer": {"transcode": True}}}
    await g.sync()
    assert not fake_go2rtc.preload


async def test_webrtc_settings_follow_the_options_and_restart_only_on_change(monkeypatch, fake_go2rtc):
    _hub, g = await _hub_with_go2rtc(monkeypatch, options={"webrtc_host": "10.0.0.5", "webrtc_port": 18555})
    assert fake_go2rtc.config["webrtc"] == {"listen": ":18555", "candidates": ["10.0.0.5:18555"]}
    assert fake_go2rtc.restarts == 1
    assert f"{REO}:sub" in fake_go2rtc.streams  # registered again after the restart
    assert await g.apply_webrtc_config() is False and fake_go2rtc.restarts == 1


async def test_default_webrtc_settings_need_no_restart(monkeypatch, fake_go2rtc):
    await _hub_with_go2rtc(monkeypatch)
    assert fake_go2rtc.restarts == 0


async def test_go2rtc_down_retries_setup(monkeypatch, fake_go2rtc):
    fake_go2rtc.down = True
    with pytest.raises(live_video.DeviceUnavailable):
        await _hub_with_go2rtc(monkeypatch)


# -- secrets ------------------------------------------------------------------------

def test_redact_removes_user_info_credential_parameters_and_known_secrets():
    text = (f"rtsp://admin:{CAM_PASSWORD}@hub.test/x http://h/flv?user=admin&password={CAM_PASSWORD}&x=1 "
            f"and {CAM_PASSWORD} alone")
    out = live_video.redact(text, live_video.source_secrets(f"rtsp://admin:{CAM_PASSWORD}@hub.test/x"))
    assert CAM_PASSWORD not in out and "admin:" not in out
    assert out.startswith("*** ") and "x=1" in out  # the whole source URL is a secret
    assert live_video.redact(f"rtsp://admin:{CAM_PASSWORD}@other.test/y") == "rtsp://***@other.test/y"


async def test_a_refusing_go2rtc_never_puts_the_password_in_logs_or_status(monkeypatch, fake_go2rtc, caplog):
    caplog.set_level(logging.DEBUG)
    fake_go2rtc.refuse = True
    _hub, g = await _hub_with_go2rtc(monkeypatch, options={"cameras": {"camera.printer": {"transcode": True}}})
    assert g.describe(REO) is None
    status = json.dumps(g.status())
    assert "refused" in status
    for secret in (CAM_PASSWORD, MJPEG_PASSWORD):
        assert secret not in caplog.text and secret not in status
    assert "Preview_01" not in caplog.text  # not even the path of the URL is logged


# -- the API ---------------------------------------------------------------------------

class MemberAuth(Auth):
    def authenticate(self, token):
        if token == "member-token":
            return Principal(user_id="u1", name="Member", is_admin=False)
        return super().authenticate(token)


@pytest.fixture
def api(tmp_path, monkeypatch, fake_go2rtc):
    hub, g = asyncio.run(_hub_with_go2rtc(monkeypatch))
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub.store = store
    app = build_app(Runtime(hub, store), MemberAuth(store, SECRET))
    with TestClient(app) as c:
        yield c, hub, g


def test_camera_list_reports_real_time_video_from_what_go2rtc_has(api):
    c, _hub, _g = api
    cams = {x["entity_id"]: x for x in c.get("/api/homehub/cameras", headers=MEMBER).json()}
    assert cams[REO]["realtime"]["modes"] == ["webrtc", "mse", "mp4"]
    assert cams[REO]["realtime"]["qualities"] == ["sub", "main"]
    assert "realtime" not in cams["camera.printer"] and "realtime" not in cams["camera.garage_door_camera"]


def test_webrtc_offer_reaches_go2rtc_for_the_cameras_stream_and_the_answer_comes_back(api, fake_go2rtc):
    c, _hub, _g = api
    offer = {"type": "offer", "sdp": "v=0\r\noffer\r\n"}
    assert c.post(f"/api/homehub/cameras/{REO}/live/webrtc", json=offer).status_code == 401
    r = c.post(f"/api/homehub/cameras/{REO}/live/webrtc?quality=main", json=offer, headers=MEMBER)
    assert r.status_code == 200 and r.json() == {"type": "answer", "sdp": "v=0\r\nanswer\r\n"}
    assert ("POST", "/api/webrtc", {"src": f"{REO}:main"}) in fake_go2rtc.calls
    r = c.post(f"/api/homehub/cameras/{REO}/live/webrtc?quality=bogus", content=b"v=0\r\noffer\r\n",
               headers={**MEMBER, "Content-Type": "application/sdp"})
    assert r.status_code == 201 and r.headers["content-type"].startswith("application/sdp")
    assert fake_go2rtc.calls[-1][2] == {"src": f"{REO}:sub"}  # an unknown quality plays the default


def test_webrtc_refuses_junk_and_cameras_go2rtc_cannot_play(api):
    c, _hub, _g = api
    url = f"/api/homehub/cameras/{REO}/live/webrtc"
    assert c.post(url, json={"type": "answer", "sdp": "x"}, headers=MEMBER).status_code == 400
    assert c.post(url, content=b"x", headers={**MEMBER, "Content-Type": "text/plain"}).status_code == 415
    assert c.post(url, content=b"x" * (live_video_api.MAX_OFFER_BYTES + 1),
                  headers={**MEMBER, "Content-Type": "application/sdp"}).status_code == 400
    r = c.post("/api/homehub/cameras/camera.printer/live/webrtc", json={"type": "offer", "sdp": "v=0"},
               headers=MEMBER)
    assert r.status_code == 409 and "MJPEG" in r.json()["message"]
    assert c.post("/api/homehub/cameras/camera.nope/live/webrtc", json={"type": "offer", "sdp": "v=0"},
                  headers=MEMBER).status_code == 404


def test_no_go2rtc_means_503(api):
    c, hub, g = api
    hub.drivers.pop(g.entry.entry_id)
    r = c.post(f"/api/homehub/cameras/{REO}/live/webrtc", json={"type": "offer", "sdp": "v=0"}, headers=MEMBER)
    assert r.status_code == 503
    assert "realtime" not in c.get("/api/homehub/cameras", headers=MEMBER).json()[0]


def test_settings_are_admin_only_and_carry_no_secret(api):
    c, _hub, _g = api
    assert c.get("/hub/live-video", headers=MEMBER).status_code == 401
    body = c.get("/hub/live-video", headers=H).json()
    assert body["configured"] is True and body["enabled"] is True and body["server"]["version"] == "1.9.15"
    reo = next(x for x in body["cameras"] if x["entity_id"] == REO)
    assert reo["registered"] == ["main", "sub"] and reo["battery"] is True
    text = json.dumps(body)
    assert CAM_PASSWORD not in text and MJPEG_PASSWORD not in text and "rtsp://" not in text


def test_settings_change_validates_saves_and_reloads(api, fake_go2rtc):
    c, hub, _g = api
    assert c.put("/hub/live-video", json={"webrtc_port": 80}, headers=H).status_code == 400
    assert c.put("/hub/live-video", json={"cameras": {"light.x": {}}}, headers=H).status_code == 400
    assert c.put("/hub/live-video", json={"url": "ftp://x"}, headers=H).status_code == 400
    assert c.put("/hub/live-video", json={"webrtc_port": 18555}, headers=MEMBER).status_code == 401
    r = c.put("/hub/live-video", json={"webrtc_host": "10.0.0.9", "webrtc_port": 18555,
                                       "cameras": {REO: {"quality": "main"}}, "password": "go2rtc-pw"},
              headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["options"] == {"webrtc_host": "10.0.0.9", "webrtc_port": 18555, "default_quality": "sub"}
    assert body["has_password"] is True and "go2rtc-pw" not in r.text
    entry = hub.config_entries["e_go2rtc"]
    assert entry.options["cameras"][REO] == {"quality": "main"} and entry.state == "loaded"
    assert fake_go2rtc.config["webrtc"]["candidates"] == ["10.0.0.9:18555"]
    stored = {e.entry_id: e for e in hub.store.load_config_entries()}["e_go2rtc"]
    assert stored.data["password"] == "go2rtc-pw" and stored.options["webrtc_port"] == 18555
    r = c.put("/hub/live-video", json={"enabled": False}, headers=H)
    assert r.json()["enabled"] is False and live_video.service(hub) is None


def test_settings_create_the_integration_when_there_is_none(api, fake_go2rtc):
    c, hub, g = api
    hub.drivers.pop(g.entry.entry_id)
    hub.config_entries.pop(g.entry.entry_id)
    assert c.get("/hub/live-video", headers=H).json()["configured"] is False
    r = c.put("/hub/live-video", json={"url": GO2RTC_URL, "enabled": True}, headers=H)
    assert r.status_code == 200 and r.json()["configured"] is True
    entry = next(e for e in hub.config_entries.values() if e.domain == "go2rtc")
    assert entry.data == {"url": GO2RTC_URL} and entry.source == "user"


# -- the WebSocket relay -------------------------------------------------------------------

class _Msg:
    def __init__(self, type_, data):
        self.type, self.data = type_, data


class FakeUpstream:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent: list[str] = []
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            await asyncio.sleep(0.05)
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def send_str(self, text):
        self.sent.append(text)

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, incoming):
        self._incoming = list(incoming)
        self.got: list = []

    async def receive(self):
        if self._incoming:
            return self._incoming.pop(0)
        await asyncio.sleep(1)
        return {"type": "websocket.disconnect"}

    async def send_bytes(self, data):
        self.got.append(data)

    async def send_text(self, text):
        self.got.append(text)


async def test_ws_relay_passes_media_down_and_only_playback_messages_up():
    import aiohttp

    upstream = FakeUpstream([_Msg(aiohttp.WSMsgType.TEXT, '{"type":"mse","value":"video/mp4"}'),
                             _Msg(aiohttp.WSMsgType.BINARY, b"\x00moof")])
    browser = FakeBrowser([
        {"type": "websocket.receive", "text": '{"type":"mse","value":"avc1.640029"}'},
        {"type": "websocket.receive", "text": '{"type":"webrtc/offer","value":"v=0"}'},
        {"type": "websocket.receive", "bytes": b"microphone"},
        {"type": "websocket.receive", "text": '{"type":"publish","value":"rtsp://x"}'},
        {"type": "websocket.receive", "text": "not json"},
    ])
    await live_video_api.relay_ws(browser, upstream)
    assert browser.got == ['{"type":"mse","value":"video/mp4"}', b"\x00moof"]
    assert upstream.sent == ['{"type":"mse","value":"avc1.640029"}', '{"type":"webrtc/offer","value":"v=0"}']
    assert upstream.closed


# -- the Reolink side ------------------------------------------------------------------------

async def test_reolink_offers_main_and_sub_without_checking_the_stream(monkeypatch):
    FakeHost.instances = []
    monkeypatch.setattr(reolink, "Host", StreamHost)
    hub, entry = reolink_hub()
    drv = reolink.ReolinkDriver(hub, entry)
    await drv.async_setup()
    got = await drv.async_camera_stream_sources(hub.entities[REO])
    assert set(got) == {"sub", "main"} and got["main"].endswith("Preview_01_main")
