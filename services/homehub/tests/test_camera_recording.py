"""Motion recording for cameras that cannot record themselves: settings, motion, clips, Nextcloud, the API.

The camera and Nextcloud are stand-ins (a fake camera driver; an in-memory WebDAV server on 127.0.0.1).
Every name, address and password here is made up.
"""
from __future__ import annotations

import io
import shutil
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from aiohttp import web
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from PIL import Image

from services.homehub import camera_recording as rec
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import ConfigEntry, DeviceEntry, Driver, EntityEntry, Hub
from services.homehub.store import Store

TZ = "Etc/GMT+7"
NEEDS_FFMPEG = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def jpeg(square_at: int | None = None, shade: int = 90, size=(320, 240)) -> bytes:
    """A flat grey picture, with a white square at x=``square_at`` (something moving)."""
    arr = np.full((size[1], size[0]), shade, dtype=np.uint8)
    if square_at is not None:
        arr[80:160, square_at:square_at + 80] = 250
    buf = io.BytesIO()
    Image.fromarray(arr, "L").convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue()


# -- settings ---------------------------------------------------------------------

def test_settings_are_checked_with_words_for_people():
    ok = rec.validate_options({"folder": "/Jarvis/Cameras/", "cameras": {"camera.garage": {
        "enabled": True, "sensitivity": 70, "pre_s": 2, "post_s": 8, "max_s": 90, "fps": 5, "retention_days": 30,
        "schedule": {"from": "22:00", "to": "06:00"}}}})
    assert ok["folder"] == "Jarvis/Cameras"
    assert ok["cameras"]["camera.garage"]["schedule"] == {"from": "22:00", "to": "06:00"}
    for bad in ({"folder": "../x"}, {"folder": ""}, {"cameras": {"light.x": {}}},
                {"cameras": {"camera.g": {"sensitivity": 0}}}, {"cameras": {"camera.g": {"fps": 2.5}}},
                {"cameras": {"camera.g": {"pre_s": True}}}, {"cameras": {"camera.g": {"zones": []}}},
                {"cameras": {"camera.g": {"schedule": {"from": "25:00", "to": "06:00"}}}},
                {"cameras": {"camera.g": {"schedule": {"from": "06:00", "to": "06:00"}}}},
                {"cameras": {"camera.g": {"post_s": 30, "max_s": 20}}}):
        with pytest.raises(ValueError):
            rec.validate_options(bad)


def test_a_cameras_settings_merge_key_by_key_and_none_removes_them():
    cur = {"folder": "A", "cameras": {"camera.g": {"enabled": True, "fps": 5}}}
    out = rec.merge_options(cur, {"cameras": {"camera.g": {"sensitivity": 40}, "camera.p": {"enabled": True}}})
    assert out["cameras"]["camera.g"] == {"enabled": True, "fps": 5, "sensitivity": 40}
    assert rec.camera_settings(out, "camera.p")["post_s"] == rec.DEFAULTS["post_s"]
    assert "camera.g" not in rec.merge_options(out, {"cameras": {"camera.g": None}})["cameras"]


def test_a_schedule_may_cross_midnight():
    night = {"from": "22:00", "to": "06:00"}
    at = lambda hm: datetime(2026, 1, 5, int(hm[:2]), int(hm[3:]))  # noqa: E731
    assert rec.in_schedule(night, at("23:30")) and rec.in_schedule(night, at("05:59"))
    assert not rec.in_schedule(night, at("06:00")) and not rec.in_schedule(night, at("12:00"))
    day = {"from": "08:00", "to": "17:30"}
    assert rec.in_schedule(day, at("08:00")) and not rec.in_schedule(day, at("17:30"))
    assert rec.in_schedule(None, at("03:00"))


# -- motion -----------------------------------------------------------------------

def test_a_still_picture_is_no_motion_and_something_moving_is():
    d = rec.MotionDetector(60)
    still = rec.gray_frame(jpeg())
    assert not any(d.feed(still) for _ in range(5))
    hits = [d.feed(rec.gray_frame(jpeg(square_at=x))) for x in (20, 60, 100, 140)]
    assert hits[0] is False and any(hits[1:])  # two frames in a row before it counts


def test_lights_coming_on_are_not_motion():
    d = rec.MotionDetector(100)
    for _ in range(3):
        d.feed(rec.gray_frame(jpeg(shade=60)))
    assert not d.feed(rec.gray_frame(jpeg(shade=180))) and not d.feed(rec.gray_frame(jpeg(shade=180)))


def test_a_lower_sensitivity_needs_more_of_the_picture_to_change():
    low_pixel, low_area = rec.thresholds(10)
    high_pixel, high_area = rec.thresholds(90)
    assert low_pixel > high_pixel and low_area > high_area
    still = np.full((72, 96), 90.0, dtype=np.float32)

    def at(x):  # something small (about 1% of the picture) at x
        frame = still.copy()
        frame[30:38, x:x + 10] = 250.0
        return frame

    for sensitivity, expected in ((95, True), (10, False)):
        d = rec.MotionDetector(sensitivity)
        d.feed(still)
        assert any([d.feed(at(x)) for x in (10, 30, 50, 70)]) is expected


def test_whole_pictures_come_out_of_an_mjpeg_stream_however_it_is_cut():
    a, b = jpeg(), jpeg(square_at=10)
    stream = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + a + b"\r\n--frame\r\n\r\n" + b + b"\r\n"
    s = rec.JpegSplitter()
    out = []
    for i in range(0, len(stream), 777):
        out += s.feed(stream[i:i + 777])
    assert out == [a, b]


# -- clips ------------------------------------------------------------------------

def test_a_clip_name_carries_its_start_and_length():
    tz = ZoneInfo(TZ)
    start = datetime(2026, 11, 13, 9, 30, 5, tzinfo=tz)
    name = rec.clip_name(start, 41.6)
    assert name == "20261113-093005_42s_motion.mp4"
    assert rec.parse_clip_name(name, tz) == (start, 42, "motion")
    assert rec.parse_clip_name("../20261113-093005_42s_motion.mp4", tz) is None
    assert rec.parse_clip_name("20261399-093005_42s_motion.mp4", tz) is None


def test_a_clip_keeps_real_time_between_frames(tmp_path):
    c = rec.Clip(tmp_path, 100.0)
    for at in (100.0, 100.25, 101.0):
        c.add(at, b"x")
    lines = c.concat_list(0.25).splitlines()
    assert lines[:6] == ["file '000000.jpg'", "duration 0.250", "file '000001.jpg'", "duration 0.750",
                         "file '000002.jpg'", "duration 0.250"]
    assert c.seconds == 1.0


@NEEDS_FFMPEG
async def test_frames_become_an_mp4_every_browser_plays(tmp_path):
    c = rec.Clip(tmp_path, 0.0)
    for i in range(8):
        c.add(i * 0.25, jpeg(square_at=10 + i * 20, size=(161, 121)))  # odd sizes: H.264 needs even ones
    out = tmp_path / "clip.mp4"
    await c.encode(out, 4)
    data = out.read_bytes()
    assert data[4:8] == b"ftyp" and b"avc1" in data[:4096]  # H.264, moov first (faststart)


# -- stand-ins: a camera and Nextcloud ----------------------------------------------

class FakeCamera(Driver):
    platform = "esphome"

    def __init__(self, hub, entry, frames=None):
        super().__init__(hub, entry)
        self.frames = frames or [jpeg()]
        self.i = 0

    async def async_camera_image(self, entity):
        frame = self.frames[min(self.i, len(self.frames) - 1)]
        self.i += 1
        return frame

    def camera_info(self, entity):
        return {"battery": entity.entity_id == "camera.porch"}


class FakeNextcloud:
    """WebDAV in memory: MKCOL, PUT, PROPFIND (depth 1), GET with ranges, DELETE."""

    def __init__(self, password="app-pass"):
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {""}
        self.password = password
        self.down = False

    def _path(self, request):
        prefix = "/remote.php/dav/files/jarvis"
        assert request.path.startswith(prefix)
        return request.path[len(prefix):].strip("/")

    async def handle(self, request: web.Request) -> web.StreamResponse:
        if self.down:
            return web.Response(status=503)
        if request.headers.get("Authorization") != __import__("aiohttp").encode_basic_auth("jarvis", self.password):
            return web.Response(status=401)
        path = self._path(request)
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        if request.method == "MKCOL":
            if path in self.dirs:
                return web.Response(status=405)
            if parent not in self.dirs:
                return web.Response(status=409)
            self.dirs.add(path)
            return web.Response(status=201)
        if request.method == "PUT":
            if parent not in self.dirs:
                return web.Response(status=409)
            self.files[path] = await request.read()
            return web.Response(status=201)
        if request.method == "PROPFIND":
            if path not in self.dirs:
                return web.Response(status=404)
            kids = [d.rsplit("/", 1)[-1] for d in self.dirs if d and (d.rsplit("/", 1)[0] if "/" in d else "") == path]
            files = {p.rsplit("/", 1)[-1]: len(b) for p, b in self.files.items()
                     if (p.rsplit("/", 1)[0] if "/" in p else "") == path}
            base = f"/remote.php/dav/files/jarvis/{path}".rstrip("/")
            parts = [f"<d:response><d:href>{base}/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/>"
                     f"</d:resourcetype></d:prop></d:propstat></d:response>"]
            parts += [f"<d:response><d:href>{base}/{n}/</d:href><d:propstat><d:prop><d:resourcetype>"
                      f"<d:collection/></d:resourcetype></d:prop></d:propstat></d:response>" for n in kids]
            parts += [f"<d:response><d:href>{base}/{n}</d:href><d:propstat><d:prop><d:resourcetype/>"
                      f"<d:getcontentlength>{size}</d:getcontentlength></d:prop></d:propstat></d:response>"
                      for n, size in files.items()]
            return web.Response(status=207, content_type="application/xml",
                                text=f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">{"".join(parts)}'
                                     "</d:multistatus>")
        if request.method == "GET":
            if path not in self.files:
                return web.Response(status=404)
            body = self.files[path]
            rng = request.headers.get("Range")
            if rng:
                first, last = rng.removeprefix("bytes=").split("-")
                first, last = int(first), int(last or len(body) - 1)
                return web.Response(status=206, body=body[first:last + 1], content_type="video/mp4",
                                    headers={"Content-Range": f"bytes {first}-{last}/{len(body)}"})
            return web.Response(body=body, content_type="video/mp4")
        if request.method == "DELETE":
            self.files = {p: b for p, b in self.files.items() if p != path and not p.startswith(path + "/")}
            self.dirs = {d for d in self.dirs if d != path and not d.startswith(path + "/")}
            return web.Response(status=204)
        return web.Response(status=405)


@pytest.fixture
async def nextcloud():
    fake = FakeNextcloud()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", fake.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    fake.url = f"http://127.0.0.1:{port}"
    yield fake
    await runner.cleanup()


def recording_hub(tmp_path, url, cameras=None, password="app-pass"):
    hub = Hub(time_zone=TZ)
    hub.data_dir = str(tmp_path)
    cam_entry = ConfigEntry("esp1", "esphome", "Garage cam")
    hub.config_entries["esp1"] = cam_entry
    hub.devices["d1"] = DeviceEntry(id="d1", name="Garage cam", config_entries=["esp1"])
    for eid in ("camera.garage", "camera.porch"):
        hub.entities[eid] = EntityEntry(eid, "esphome", eid, "esp1", "d1", original_name=eid.split(".")[1].title())
    hub.drivers["esp1"] = FakeCamera(hub, cam_entry)
    entry = ConfigEntry("rec1", rec.DOMAIN, "Motion recording",
                        {"url": url, "username": "jarvis", "password": password},
                        options={"cameras": cameras if cameras is not None else
                                 {"camera.garage": {"enabled": True, "pre_s": 1, "post_s": 2, "fps": 4}}})
    hub.config_entries["rec1"] = entry
    drv = rec.CameraRecordingDriver(hub, entry)
    hub.drivers["rec1"] = drv
    return hub, drv


async def record_motion(drv, start=1_763_051_400.0):
    """Feed one camera's watcher: quiet, something moving, quiet again until the clip ends."""
    w = rec.CameraWatcher(drv, "camera.garage", rec.camera_settings(drv.entry.options, "camera.garage"))
    drv.watchers["camera.garage"] = w
    t = start
    for _ in range(6):
        await w.on_frame(t, jpeg())
        t += 0.25
    for x in range(10, 210, 20):
        await w.on_frame(t, jpeg(square_at=x))
        t += 0.25
    for _ in range(14):
        await w.on_frame(t, jpeg())
        t += 0.25
    import asyncio

    await asyncio.gather(*drv._tasks)
    return w


# -- the recorder, end to end ---------------------------------------------------------

@NEEDS_FFMPEG
async def test_motion_makes_a_clip_in_nextcloud_listed_and_playable(tmp_path, nextcloud):
    hub, drv = recording_hub(tmp_path, nextcloud.url)
    await drv.async_setup()
    for w in list(drv.watchers.values()):
        await w.stop()  # the real loop is not wanted here: frames are fed by hand
    drv.watchers.clear()
    try:
        w = await record_motion(drv)
        assert w.status.clips == 1 and not w.status.recording
        [(path, data)] = list(nextcloud.files.items())
        assert path.startswith("Jarvis/Cameras/garage/") and data[4:8] == b"ftyp"
        day, name = path.split("/")[3:]
        start, seconds, trigger = rec.parse_clip_name(name, ZoneInfo(TZ))
        assert day == start.date().isoformat() and trigger == "motion"
        # It begins before the motion (pre_s) and ends post_s after it.
        assert start.timestamp() <= 1_763_051_400.0 + 1.5 and 3 <= seconds <= 7
        assert not list((tmp_path / "camera-recordings" / "pending").rglob("*.mp4"))
        # The motion sensor, on the camera's device, was on and is off again.
        motion = hub.states.get("binary_sensor.garage_motion")
        assert motion.state == "off" and motion.attributes["device_class"] == "motion"
        assert hub.entities["binary_sensor.garage_motion"].device_id == "d1"

        ent = hub.entities["camera.garage"]
        listed = await drv.async_list_recordings(ent, start - timedelta(hours=1), start + timedelta(hours=1))
        assert [r["file"] for r in listed] == [name] and listed[0]["triggers"] == ["motion"]
        assert await drv.async_list_recordings(ent, start + timedelta(hours=2), start + timedelta(hours=3)) == []
        got = await drv.async_open_recording(ent, name, range_header="bytes=0-99")
        body = b"".join([c async for c in got.body])
        assert got.status == 206 and body == data[:100] and got.headers["Content-Range"].endswith(f"/{len(data)}")
        with pytest.raises(rec.NextcloudError):
            await drv.async_open_recording(ent, "../../etc/passwd", range_header=None)
    finally:
        await drv.async_unload()


@NEEDS_FFMPEG
async def test_a_clip_nextcloud_cannot_take_waits_on_the_hub_and_still_plays(tmp_path, nextcloud):
    hub, drv = recording_hub(tmp_path, nextcloud.url)
    await drv.async_setup()
    for w in list(drv.watchers.values()):
        await w.stop()
    drv.watchers.clear()
    try:
        nextcloud.down = True
        await record_motion(drv)
        [local] = list((tmp_path / "camera-recordings" / "pending").rglob("*.mp4"))
        assert nextcloud.files == {} and "kept on the hub" in drv.last_error
        ent = hub.entities["camera.garage"]
        start = rec.parse_clip_name(local.name, ZoneInfo(TZ))[0]
        listed = await drv.async_list_recordings(ent, start - timedelta(minutes=5), start + timedelta(minutes=5))
        assert [r["file"] for r in listed] == [local.name]
        got = await drv.async_open_recording(ent, local.name, range_header="bytes=10-")
        assert got.status == 206 and b"".join([c async for c in got.body]) == local.read_bytes()[10:]
        nextcloud.down = False
        await drv.upload_pending()
        assert not local.exists() and len(nextcloud.files) == 1 and drv.last_error is None
    finally:
        await drv.async_unload()


@NEEDS_FFMPEG
async def test_old_days_are_deleted_after_the_retention(tmp_path, nextcloud):
    _hub, drv = recording_hub(tmp_path, nextcloud.url, cameras={"camera.garage": {"enabled": False,
                                                                                 "retention_days": 7}})
    await drv.async_setup()
    try:
        today = datetime.now(ZoneInfo(TZ)).date()
        for age in (0, 6, 7, 30):
            day = (today - timedelta(days=age)).isoformat()
            await drv.nextcloud.ensure_folder(f"Jarvis/Cameras/garage/{day}")
        await drv.prune()
        left = sorted(d.rsplit("/", 1)[-1] for d in nextcloud.dirs if d.startswith("Jarvis/Cameras/garage/"))
        assert left == sorted((today - timedelta(days=a)).isoformat() for a in (0, 6))
    finally:
        await drv.async_unload()


@NEEDS_FFMPEG
async def test_setup_says_when_nextcloud_refuses_the_login(tmp_path, nextcloud):
    _hub, drv = recording_hub(tmp_path, nextcloud.url, password="wrong")
    with pytest.raises(rec.NextcloudError, match="login"):
        await drv.async_setup()


@NEEDS_FFMPEG
async def test_only_enabled_cameras_are_watched_and_never_a_battery_camera(tmp_path, nextcloud):
    _hub, drv = recording_hub(tmp_path, nextcloud.url, cameras={
        "camera.garage": {"enabled": True}, "camera.porch": {"enabled": True}})
    await drv.async_setup()
    try:
        assert set(drv.watchers) == {"camera.garage"}
        assert rec.ineligible(drv.hub, "camera.porch").startswith("it runs on a battery")
        drv.entry.options = rec.merge_options(drv.entry.options, {"cameras": {"camera.garage": {"enabled": False}}})
        await drv.sync()
        assert drv.watchers == {}
    finally:
        await drv.async_unload()


# -- the settings API and the Cameras page ---------------------------------------------

class FakeRuntime:
    def __init__(self, hub, store):
        self.hub, self.store = hub, store
        self.started: list[str] = []

    async def stop_entry(self, entry_id):
        drv = self.hub.drivers.pop(entry_id, None)
        if drv is not None:
            await drv.async_unload()

    def start_entry(self, entry):
        self.started.append(entry.entry_id)
        entry.state = "loaded"


SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}


@pytest.fixture
def api(tmp_path):
    hub, _drv = recording_hub(tmp_path, "https://cloud.example.test", cameras={})
    del hub.config_entries["rec1"], hub.drivers["rec1"]
    hub.household_nextcloud = {"url": "https://cloud.example.test", "username": "jarvis", "password": "app-pass"}
    hub.publish("camera.garage", "idle", {})
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub.store = store
    rt = FakeRuntime(hub, store)
    app = build_app(rt, Auth(store, SECRET))
    with TestClient(app) as c:
        yield c, hub, rt


def test_settings_use_jarvis_nextcloud_and_never_show_the_password(api):
    c, hub, rt = api
    assert c.get("/hub/camera-recording").status_code == 401
    first = c.get("/hub/camera-recording", headers=H).json()
    assert first["configured"] is False and first["household_account"] == {
        "url": "https://cloud.example.test", "username": "jarvis"}
    cams = {x["entity_id"]: x for x in first["cameras"]}
    assert cams["camera.garage"]["unavailable_reason"] is None and cams["camera.garage"]["records_itself"] is False
    assert cams["camera.porch"]["unavailable_reason"].startswith("it runs on a battery")
    out = c.put("/hub/camera-recording", headers=H, json={
        "account": "household", "cameras": {"camera.garage": {"enabled": True, "sensitivity": 75}}})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["configured"] and body["account"] == "household" and body["has_password"]
    assert "app-pass" not in out.text
    entry = next(e for e in hub.config_entries.values() if e.domain == rec.DOMAIN)
    assert entry.data["password"] == "app-pass" and rt.started == [entry.entry_id]
    assert entry.options["cameras"]["camera.garage"] == {"enabled": True, "sensitivity": 75}
    assert c.put("/hub/camera-recording", headers=H, json={"cameras": {"camera.garage": {"fps": 99}}}).status_code \
        == 400


def test_a_new_setup_needs_an_account(api):
    c, hub, _rt = api
    hub.household_nextcloud = None
    r = c.put("/hub/camera-recording", headers=H, json={"account": "household"})
    assert r.status_code == 400 and "NEXTCLOUD_URL" in r.json()["message"]
    r = c.put("/hub/camera-recording", headers=H, json={"cameras": {"camera.garage": {"enabled": True}}})
    assert r.status_code == 400
    r = c.put("/hub/camera-recording", headers=H, json={
        "account": "custom", "url": "https://user@cloud.example.test", "username": "x", "password": "y"})
    assert r.status_code == 400


def test_a_recorded_camera_has_a_recordings_tab(api, tmp_path):
    c, hub, _rt = api
    entry = ConfigEntry("rec1", rec.DOMAIN, "Motion recording",
                        {"url": "https://cloud.example.test", "username": "jarvis", "password": "app-pass"},
                        options={"cameras": {"camera.garage": {"enabled": True}}})
    hub.config_entries["rec1"] = entry
    hub.drivers["rec1"] = rec.CameraRecordingDriver(hub, entry)
    cams = {x["entity_id"]: x for x in c.get("/api/homehub/cameras", headers=H).json()}
    assert cams["camera.garage"]["recordings"] is True and cams["camera.porch"]["recordings"] is False
    assert c.get("/api/homehub/cameras/camera.porch/recordings", headers=H).status_code == 404


def test_main_reads_the_household_account_once_from_the_environment(monkeypatch):
    for k in ("NEXTCLOUD_URL", "NEXTCLOUD_USER", "NEXTCLOUD_PASS"):
        monkeypatch.setenv(k, f"value-{k}")
    assert rec.household_account_from_env() == {"url": "value-NEXTCLOUD_URL", "username": "value-NEXTCLOUD_USER",
                                                "password": "value-NEXTCLOUD_PASS"}
    monkeypatch.delenv("NEXTCLOUD_PASS")
    assert rec.household_account_from_env() is None
