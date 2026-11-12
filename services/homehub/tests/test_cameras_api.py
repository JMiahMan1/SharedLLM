"""The hub's camera API (list, live stream, recordings) and Reolink recordings, with no network."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from reolink_aio.enums import VodRequestType
from reolink_aio.typings import VOD_file, VOD_trigger

from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import DeviceUnavailable, Driver, EntityEntry, EntityNotFound
from services.homehub.drivers import reolink
from services.homehub.runtime import Runtime
from services.homehub.store import Store
from services.homehub.tests.test_drivers_cameras import CAM2, HUB, JPEG, FakeHost, reolink_hub

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}
#: The fake hub's clock: UTC-7 all year, like a place with no daylight saving.
HUB_TZ = timezone(timedelta(hours=-7))


def _t(y, mo, d, h, mi, s):
    return {"year": y, "mon": mo, "day": d, "hour": h, "min": mi, "sec": s}


def vod(start, end, name, triggers=VOD_trigger.NONE, stream="sub"):
    f = VOD_file({"StartTime": _t(*start), "EndTime": _t(*end), "PlaybackTime": _t(*start), "name": name,
                  "size": "2048", "type": stream}, HUB_TZ)
    f.bc_triggers = triggers
    return f


NAME_A = "Mp4Record/2026-11-10/RecM07_DST20261110_081500_081530_0_A714C0A000_21E67C.mp4"
NAME_B = "Mp4Record/2026-11-10/RecM07_DST20261110_201000_201045_0_A714C0A000_21E67C.mp4"
NAME_OTHER_DAY = "Mp4Record/2026-11-09/RecM07_DST20261109_230000_230020_0_A714C0A000_21E67C.mp4"


class VodHost(FakeHost):
    """FakeHost plus the hub's recording search and playback."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.is_hub, self.is_nvr = True, True
        self.vods = {
            (0, "2026-11-10"): [vod((2026, 11, 10, 8, 15, 0), (2026, 11, 10, 8, 15, 30), NAME_A,
                                    VOD_trigger.MOTION | VOD_trigger.PERSON),
                                vod((2026, 11, 10, 20, 10, 0), (2026, 11, 10, 20, 10, 45), NAME_B,
                                    VOD_trigger.VEHICLE)],
            (0, "2026-11-09"): [vod((2026, 11, 9, 23, 0, 0), (2026, 11, 9, 23, 0, 20), NAME_OTHER_DAY)],
        }
        self.searches = []

    def timezone(self):
        return HUB_TZ

    async def request_vod_files(self, channel, start, end, status_only=False, stream=None, split_time=None,
                                trigger=None):
        assert start.tzinfo is None and end.tzinfo is None  # the hub searches in its own local time
        self.searches.append((channel, start, end, stream))
        files = self.vods.get((channel, start.strftime("%Y-%m-%d")), [])
        return [], [f for f in files if start <= f.start_time.replace(tzinfo=None) <= end]

    async def get_vod_source(self, channel, filename, stream=None, request_type=VodRequestType.FLV):
        self.calls.append(("get_vod_source", channel, filename, stream, request_type))
        return "video/mp4", f"https://hub.test/cgi-bin/api.cgi?cmd={request_type.value}&source={filename}&token=T"


class FakeVodResp:
    def __init__(self, status=200, headers=None, chunks=(b"\x00\x00\x00\x18ftypmp42", b"moov")):
        self.status = status
        self.headers = headers if headers is not None else {"Content-Type": "application/octet-stream",
                                                              "Content-Length": "16"}
        self._chunks = list(chunks)
        self.released = False

        class _Content:
            async def iter_chunked(_self, _n):
                for c in self._chunks:
                    yield c

        self.content = _Content()

    def release(self):
        self.released = True


class FakeVodSession:
    closed = False

    def __init__(self, resp):
        self.resp = resp
        self.calls = []

    async def get(self, url, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        return self.resp

    async def close(self):
        self.closed = True


@pytest.fixture
def vod_host(monkeypatch):
    FakeHost.instances = []
    monkeypatch.setattr(reolink, "Host", VodHost)
    return VodHost


async def reolink_driver():
    hub, entry = reolink_hub()
    drv = reolink.ReolinkDriver(hub, entry)
    await drv.async_setup()
    hub.drivers[entry.entry_id] = drv
    return hub, drv, FakeHost.instances[-1]


# -- the Reolink driver -------------------------------------------------------------

def test_trigger_names_put_the_telling_ones_first():
    assert reolink.trigger_names(VOD_trigger.MOTION | VOD_trigger.PERSON) == ["person", "motion"]
    assert reolink.trigger_names(VOD_trigger.NONE) == []
    assert reolink.trigger_names(None) == []


def test_recording_day_reads_the_name_or_its_playback_time():
    assert reolink.recording_day(NAME_A) == datetime(2026, 11, 10)
    assert reolink.recording_day("20261110151500") == datetime(2026, 11, 10)
    assert reolink.recording_day("no-date-here.mp4") is None


async def test_listing_searches_each_local_day_and_never_wakes_a_camera(vod_host):
    hub, drv, host = await reolink_driver()
    cam = hub.entities["camera.garage_camera_1_fluent"]
    reads_before = len(host.calls)
    start = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)  # 05:00 on the 9th, hub time
    end = datetime(2026, 11, 11, 6, 0, tzinfo=UTC)  # 23:00 on the 10th, hub time
    items = await drv.async_list_recordings(cam, start, end)
    assert [s[1:3] for s in host.searches] == [
        (datetime(2026, 11, 9, 5, 0), datetime(2026, 11, 9, 23, 59, 59)),
        (datetime(2026, 11, 10, 0, 0), datetime(2026, 11, 10, 23, 0)),
    ]
    assert {s[3] for s in host.searches} == {"sub"}
    by_file = {i["file"]: i for i in items}
    assert set(by_file) == {NAME_A, NAME_B, NAME_OTHER_DAY}
    a = by_file[NAME_A]
    assert a["start"] == "2026-11-10T08:15:00-07:00" and a["end"] == "2026-11-10T08:15:30-07:00"
    assert a["duration"] == 30.0 and a["triggers"] == ["person", "motion"] and a["size"] == 2048
    assert by_file[NAME_B]["triggers"] == ["vehicle"]
    # Listing reads the hub's storage only: no state read that would wake a battery camera.
    assert not [c for c in host.calls[reads_before:] if c[0] in ("get_states", "get_snapshot")]


async def test_playing_a_listed_recording_streams_the_hubs_mp4(vod_host):
    hub, drv, host = await reolink_driver()
    cam = hub.entities["camera.garage_camera_1_fluent"]
    await drv.async_list_recordings(cam, datetime(2026, 11, 10, 7, tzinfo=UTC), datetime(2026, 11, 11, 6, tzinfo=UTC))
    resp = FakeVodResp(206, {"Content-Type": "application/octet-stream", "Content-Length": "16",
                             "Content-Range": "bytes 0-15/2048", "Accept-Ranges": "bytes", "Set-Cookie": "x"})
    drv._vod_session = FakeVodSession(resp)
    rec = await drv.async_open_recording(cam, NAME_A, range_header="bytes=0-15")
    assert rec.status == 206 and rec.content_type == "video/mp4"
    assert rec.headers == {"Content-Length": "16", "Content-Range": "bytes 0-15/2048", "Accept-Ranges": "bytes"}
    assert b"".join([c async for c in rec.body]) == b"\x00\x00\x00\x18ftypmp42moov"
    assert resp.released
    _url, headers = drv._vod_session.calls[0]
    assert headers == {"Range": "bytes=0-15"}
    # A hub is an NVR: HA's media source downloads from it.
    assert ("get_vod_source", 0, NAME_A, "sub", VodRequestType.DOWNLOAD) in host.calls


async def test_a_file_never_listed_is_found_by_listing_its_day_again(vod_host):
    hub, drv, host = await reolink_driver()
    cam = hub.entities["camera.garage_camera_1_fluent"]
    drv._vod_session = FakeVodSession(FakeVodResp())
    rec = await drv.async_open_recording(cam, NAME_B)  # as after a restart: nothing listed yet
    assert rec.status == 200
    assert host.searches  # it asked the hub


async def test_a_file_from_another_camera_or_made_up_is_refused(vod_host):
    hub, drv, _host = await reolink_driver()
    other = EntityEntry(entity_id="camera.backyard_camera_1_fluent", platform="reolink",
                        unique_id=f"{HUB}_{CAM2}_sub", config_entry_id="entry1")
    hub.entities[other.entity_id] = other
    drv._vod_session = FakeVodSession(FakeVodResp())
    with pytest.raises(EntityNotFound):
        await drv.async_open_recording(other, NAME_A)  # channel 0's file, asked of channel 1
    with pytest.raises(EntityNotFound):
        await drv.async_open_recording(hub.entities["camera.garage_camera_1_fluent"], "../../etc/passwd")
    assert not drv._vod_session.calls


async def test_an_error_page_from_the_hub_is_not_served_as_video(vod_host):
    hub, drv, _host = await reolink_driver()
    cam = hub.entities["camera.garage_camera_1_fluent"]
    resp = FakeVodResp(200, {"Content-Type": "text/html"})
    drv._vod_session = FakeVodSession(resp)
    with pytest.raises(DeviceUnavailable):
        await drv.async_open_recording(cam, NAME_A)
    assert resp.released


async def test_camera_info_says_battery_and_sleep(vod_host):
    hub, drv, _host = await reolink_driver()
    info = drv.camera_info(hub.entities["camera.garage_camera_1_fluent"])
    assert info == {"battery": True, "sleeping": False, "model": "Argus"}
    assert drv.supports_recordings(hub.entities["camera.garage_camera_1_fluent"])
    assert not drv.supports_recordings(hub.entities["siren.reolink_hub_siren"])


async def test_unload_closes_the_playback_session(vod_host):
    _hub, drv, _host = await reolink_driver()
    session = drv._vod_session = FakeVodSession(FakeVodResp())
    await drv.async_unload()
    assert session.closed


# -- the API --------------------------------------------------------------------------

class StillCam(Driver):
    """A camera with snapshots only (like an ESPHome camera)."""

    platform = "esphome"
    poll_interval = None
    snapshot_interval = 0.01

    def __init__(self, hub, entry, frames=None):
        super().__init__(hub, entry)
        self.frames = list(frames if frames is not None else [JPEG, JPEG])

    async def async_camera_image(self, entity):
        if not self.frames:
            raise DeviceUnavailable("asleep")
        return self.frames.pop(0)


class RelayCam(StillCam):
    platform = "mjpeg"

    def __init__(self, hub, entry, fail=False):
        super().__init__(hub, entry)
        self.fail = fail

    async def async_open_mjpeg_stream(self, entity):
        if self.fail:
            raise DeviceUnavailable("stream refused")

        async def chunks():
            yield b"--camboundary\r\nContent-Type: image/jpeg\r\n\r\n" + JPEG

        return "multipart/x-mixed-replace;boundary=camboundary", chunks()


@pytest.fixture
def api(tmp_path, vod_host):
    hub, entry = reolink_hub()
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub.store = store
    drv = reolink.ReolinkDriver(hub, entry)
    asyncio.run(drv.async_setup())
    hub.drivers[entry.entry_id] = drv
    from services.homehub.core import Area, ConfigEntry, DeviceEntry

    hub.areas["garage"] = Area("garage", "Garage")
    for eid, cls, platform in (("camera.garage_door_camera", StillCam, "esphome"),
                               ("camera.printer", RelayCam, "mjpeg")):
        e = ConfigEntry(f"e_{platform}", platform, platform)
        hub.config_entries[e.entry_id] = e
        hub.devices[f"d_{platform}"] = DeviceEntry(id=f"d_{platform}", name=platform, area_id="garage",
                                                   config_entries=[e.entry_id])
        hub.entities[eid] = EntityEntry(entity_id=eid, platform=platform, unique_id=eid,
                                        config_entry_id=e.entry_id, device_id=f"d_{platform}",
                                        original_name="Camera")
        hub.drivers[e.entry_id] = cls(hub, e)
        hub.publish(eid, "idle", {})
    app = build_app(Runtime(hub, store), Auth(store, SECRET))
    with TestClient(app) as c:
        yield c, hub, drv


def test_camera_list_says_what_each_camera_can_do(api):
    c, _hub, _drv = api
    assert c.get("/api/homehub/cameras").status_code == 401
    cams = {x["entity_id"]: x for x in c.get("/api/homehub/cameras", headers=H).json()}
    assert set(cams) == {"camera.garage_camera_1_fluent", "camera.garage_door_camera", "camera.printer"}
    reo = cams["camera.garage_camera_1_fluent"]
    assert reo["platform"] == "reolink" and reo["recordings"] is True and reo["state"] == "idle"
    assert reo["battery"] is True and reo["stream"] == "snapshots" and reo["snapshot_interval"] == 0.5
    assert reo["live"] == ["snapshot", "mjpeg"] and reo["area"] == "Office"
    esp = cams["camera.garage_door_camera"]
    assert esp["recordings"] is False and esp["area"] == "Garage" and esp["stream"] == "snapshots"
    assert cams["camera.printer"]["stream"] == "camera"


def test_recordings_listing_defaults_caps_and_sorts(api):
    c, _hub, _drv = api
    url = "/api/homehub/cameras/camera.garage_camera_1_fluent/recordings"
    assert c.get(url).status_code == 401
    r = c.get(url, headers=H, params={"start": "2026-11-09T00:00:00-07:00", "end": "2026-11-10T23:59:59-07:00"})
    assert r.status_code == 200
    body = r.json()
    assert [x["file"] for x in body["recordings"]] == [NAME_B, NAME_A, NAME_OTHER_DAY]  # newest first
    too_long = c.get(url, headers=H, params={"start": "2026-11-01T00:00:00Z", "end": "2026-11-10T00:00:00Z"})
    assert too_long.status_code == 400 and "7 days" in too_long.json()["message"]
    backwards = c.get(url, headers=H, params={"start": "2026-11-10T00:00:00Z", "end": "2026-11-09T00:00:00Z"})
    assert backwards.status_code == 400
    default = c.get(url, headers=H).json()
    span = datetime.fromisoformat(default["end"]) - datetime.fromisoformat(default["start"])
    assert span == timedelta(days=1)
    no_recs = c.get("/api/homehub/cameras/camera.garage_door_camera/recordings", headers=H)
    assert no_recs.status_code == 404
    assert c.get("/api/homehub/cameras/camera.nope/recordings", headers=H).status_code == 404


def test_play_streams_with_range_and_refuses_unknown_files(api):
    c, _hub, drv = api
    drv._vod_session = FakeVodSession(FakeVodResp(206, {"Content-Type": "application/octet-stream",
                                                        "Content-Length": "16",
                                                        "Content-Range": "bytes 0-15/2048"}))
    url = "/api/homehub/cameras/camera.garage_camera_1_fluent/recordings/play"
    assert c.get(url, params={"file": NAME_A}).status_code == 401
    r = c.get(url, headers={**H, "Range": "bytes=0-15"}, params={"file": NAME_A})
    assert r.status_code == 206
    assert r.headers["content-type"] == "video/mp4" and r.headers["content-range"] == "bytes 0-15/2048"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.content == b"\x00\x00\x00\x18ftypmp42moov"
    assert drv._vod_session.calls[-1][1] == {"Range": "bytes=0-15"}
    assert c.get(url, headers=H, params={"file": "Mp4Record/2026-11-10/RecM07_DST20261110_000000_000001.mp4"}
                 ).status_code == 404
    assert c.get(url, headers=H).status_code == 400


def test_snapshot_stream_ends_when_the_camera_stops_answering(api):
    c, _hub, _drv = api
    r = c.get("/api/camera_proxy_stream/camera.garage_door_camera", headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert r.content.count(b"Content-Type: image/jpeg") == 2


def test_snapshot_stream_of_a_sleeping_camera_is_an_error_up_front(api):
    c, hub, _drv = api
    hub.drivers["e_esphome"].frames = []
    r = c.get("/api/camera_proxy_stream/camera.garage_door_camera", headers=H)
    assert r.status_code == 503


def test_mjpeg_camera_stream_is_relayed_and_falls_back_to_snapshots(api):
    c, hub, _drv = api
    r = c.get("/api/camera_proxy_stream/camera.printer", headers=H)
    assert r.headers["content-type"] == "multipart/x-mixed-replace;boundary=camboundary"
    assert r.content.startswith(b"--camboundary")
    hub.drivers["e_mjpeg"].fail = True
    r = c.get("/api/camera_proxy_stream/camera.printer", headers=H)
    assert r.headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
