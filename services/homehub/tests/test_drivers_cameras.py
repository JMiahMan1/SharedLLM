"""MJPEG and Reolink cameras: states, the camera contract and Reolink's controls, with no network."""
from __future__ import annotations

import typing

import pytest

from services.homehub.core import DeviceUnavailable, ServiceNotFound
from services.homehub.drivers import mjpeg, reolink
from services.homehub.tests.conftest import make_hub

JPEG = b"\xff\xd8\xff\xe0JFIFdata\xff\xd9"


# -- a fake aiohttp session -------------------------------------------------------

class FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_chunked(self, _n):
        for c in self._chunks:
            yield c


class FakeResp:
    def __init__(self, status=200, body=b"", headers=None, chunks=None):
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.content = FakeContent(chunks if chunks is not None else [body])

    async def read(self):
        return self._body

    def release(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.closed = False
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        resp = self.routes.get(url)
        if isinstance(resp, Exception):
            raise resp
        return resp or FakeResp(404)

    async def close(self):
        self.closed = True


def mjpeg_hub(**options):
    opts = {"mjpeg_url": "http://cam.test:8080/", "still_image_url": "http://cam.test:8081/",
            "username": "", "password": "", "authentication": "basic", "verify_ssl": False}
    opts.update(options)
    hub, entry = make_hub("mjpeg", entities=[{"entity_id": "camera.garage", "unique_id": "E1",
                                              "original_name": "Garage Camera"}], options=opts)
    return hub, entry


# -- mjpeg ------------------------------------------------------------------------

def test_extract_jpeg_uses_content_length_over_an_inner_end_marker():
    frame = b"\xff\xd8thumb\xff\xd9rest-of-frame\xff\xd9"
    buf = b"--b\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame) + frame + b"\r\n--b"
    got, _ = mjpeg.extract_jpeg(buf)
    assert got == frame


def test_extract_jpeg_without_length_and_incomplete():
    assert mjpeg.extract_jpeg(b"--b\r\n\r\n" + JPEG)[0] == JPEG
    assert mjpeg.extract_jpeg(b"--b\r\n\r\n\xff\xd8partial")[0] is None


async def test_mjpeg_publishes_idle_with_a_token_picture():
    hub, entry = mjpeg_hub()
    drv = mjpeg.MjpegDriver(hub, entry)
    await drv.async_setup()
    st = hub.states.get("camera.garage")
    assert st.state == "idle"
    token = st.attributes["access_token"]
    assert st.attributes["entity_picture"] == f"/api/camera_proxy/camera.garage?token={token}"
    assert drv.access_tokens("camera.garage") == [token]
    drv.tokens.rotate("camera.garage")
    assert len(drv.access_tokens("camera.garage")) == 2  # the newest two stay valid


async def test_mjpeg_still_image_then_stream_fallback():
    hub, entry = mjpeg_hub()
    drv = mjpeg.MjpegDriver(hub, entry)
    ent = hub.entities["camera.garage"]
    drv._session = FakeSession({"http://cam.test:8081/": FakeResp(200, JPEG)})
    assert await drv.async_camera_image(ent) == JPEG

    stream = [b"--123\r\nContent-Type: image/jpeg\r\n\r\n", JPEG[:6], JPEG[6:] + b"\r\n--123"]
    drv._session = FakeSession({
        "http://cam.test:8081/": FakeResp(500),
        "http://cam.test:8080/": FakeResp(200, headers={"Content-Type": "multipart/x-mixed-replace"},
                                          chunks=stream),
    })
    assert await drv.async_camera_image(ent) == JPEG


async def test_mjpeg_unavailable_when_nothing_answers():
    hub, entry = mjpeg_hub()
    drv = mjpeg.MjpegDriver(hub, entry)
    drv._session = FakeSession({})
    with pytest.raises(DeviceUnavailable):
        await drv.async_camera_image(hub.entities["camera.garage"])


async def test_mjpeg_stream_url_carries_basic_credentials():
    hub, entry = mjpeg_hub(username="u ser", password="p@ss")
    drv = mjpeg.MjpegDriver(hub, entry)
    url = await drv.async_camera_stream_url(hub.entities["camera.garage"])
    assert url == "http://u%20ser:p%40ss@cam.test:8080/"
    hub2, entry2 = mjpeg_hub()
    assert await mjpeg.MjpegDriver(hub2, entry2).async_camera_stream_url(hub2.entities["camera.garage"]) \
        == "http://cam.test:8080/"


async def test_mjpeg_has_no_services():
    hub, entry = mjpeg_hub()
    with pytest.raises(ServiceNotFound):
        await mjpeg.MjpegDriver(hub, entry).async_call(hub.entities["camera.garage"], "camera", "snapshot", {})


# -- reolink ----------------------------------------------------------------------

HUB = "9527000ABCDE12FG"
CAM1, CAM2 = "95270006CAMERA01", "9527000KR5QE1YQF"


class FakeBaichuan:
    def __init__(self):
        self.callbacks = {}
        self.events_active = True
        self.subscribed = False

    def register_callback(self, cid, cb, cmd_id=None, channel=None):
        self.callbacks[cid] = cb

    def unregister_callback(self, cid):
        self.callbacks.pop(cid, None)

    async def subscribe_events(self):
        self.subscribed = True

    async def unsubscribe_events(self):
        self.subscribed = False


class FakeHost:
    instances: typing.ClassVar[list] = []

    def __init__(self, host, username, password, **kw):
        self.address, self.kw = host, kw
        self.uid = HUB
        self.channels = [0, 1]
        self.stream_channels = [0, 1]
        self.baichuan = FakeBaichuan()
        self.motion = {0: False, 1: True}
        self.ai = {(0, "people"): True}
        self.led = {0: True, 1: False}
        self.state_light = True
        self.calls = []
        self.online = {0: True, 1: True}
        FakeHost.instances.append(self)

    async def get_host_data(self):
        self.calls.append(("get_host_data",))

    async def get_states(self, wake=True, cmd_list=None):
        self.calls.append(("get_states", wake))

    async def get_motion_state_all_ch(self):
        self.calls.append(("motion_all",))

    sw_version = "v3.0.0.1"

    def camera_sw_version(self, ch):
        return "v3.1.0.2"

    async def check_new_firmware(self, ch_list=None):
        return {}

    async def logout(self):
        self.calls.append(("logout",))

    def camera_uid(self, ch):
        return {0: CAM1, 1: CAM2}[ch]

    def camera_model(self, ch):
        return "Argus"

    def camera_online(self, ch):
        return self.online[ch]

    def supported(self, ch, cap):
        return cap == "battery"

    def motion_detected(self, ch):
        return self.motion[ch]

    def ai_detected(self, ch, kind):
        return self.ai.get((ch, kind), False)

    def sleeping(self, ch):
        return ch == 1

    def battery_percentage(self, ch):
        return {0: 87, 1: None}[ch]

    def status_led_enabled(self, ch):
        return self.led[ch]

    def ir_enabled(self, ch):
        return True

    def volume(self, ch):
        return 70

    def ai_sensitivity(self, ch, kind):
        return 55

    def daynight_state(self, ch):
        return "Black&White"

    async def set_siren(self, ch, enable, duration):
        self.calls.append(("set_siren", ch, enable, duration))

    async def set_status_led(self, ch, on):
        self.calls.append(("set_status_led", ch, on))
        self.led[ch] = on

    async def set_state_light(self, on):
        self.calls.append(("set_state_light", on))
        self.state_light = on

    async def set_ir_lights(self, ch, on):
        self.calls.append(("set_ir_lights", ch, on))

    async def set_volume(self, ch, volume=None):
        self.calls.append(("set_volume", ch, volume))

    async def set_daynight(self, ch, value):
        self.calls.append(("set_daynight", ch, value))

    async def get_snapshot(self, ch, stream):
        self.calls.append(("get_snapshot", ch, stream))
        return JPEG

    async def get_stream_source(self, ch, stream):
        return f"rtsp://hub/{ch}/{stream}"


def reolink_hub(options=None):
    ents = [
        {"entity_id": "camera.garage_camera_1_fluent", "unique_id": f"{HUB}_{CAM1}_sub"},
        {"entity_id": "binary_sensor.garage_camera_1_motion", "unique_id": f"{HUB}_{CAM1}_motion"},
        {"entity_id": "binary_sensor.garage_camera_1_person", "unique_id": f"{HUB}_{CAM1}_person"},
        {"entity_id": "binary_sensor.backyard_camera_1_motion", "unique_id": f"{HUB}_{CAM2}_motion"},
        {"entity_id": "binary_sensor.backyard_camera_1_sleep_status", "unique_id": f"{HUB}_{CAM2}_sleep"},
        {"entity_id": "sensor.garage_camera_1_battery", "unique_id": f"{HUB}_{CAM1}_battery_percent"},
        {"entity_id": "light.garage_camera_1_status_led", "unique_id": f"{HUB}_{CAM1}_status_led"},
        {"entity_id": "light.reolink_hub_status_led", "unique_id": f"{HUB}_hub_status_led"},
        {"entity_id": "siren.garage_camera_1_siren", "unique_id": f"{HUB}_{CAM1}_siren"},
        {"entity_id": "siren.reolink_hub_siren", "unique_id": f"{HUB}_siren"},
        {"entity_id": "switch.garage_camera_1_infrared_lights_in_night_mode", "unique_id": f"{HUB}_{CAM1}_ir_lights"},
        {"entity_id": "number.garage_camera_1_volume", "unique_id": f"{HUB}_{CAM1}_volume"},
        {"entity_id": "number.garage_camera_1_ai_person_sensitivity",
         "unique_id": f"{HUB}_{CAM1}_ai_person_sensititvity"},
        {"entity_id": "select.garage_camera_1_day_night_mode", "unique_id": f"{HUB}_{CAM1}_day_night_mode"},
    ]
    data = {"host": "ReoLink-Hub.local", "port": 443, "use_https": True, "username": "admin", "password": "x",
            "baichuan_port": 9000, "baichuan_connection": "tcp", "baichuan_only": False, "uid": HUB}
    if options is None:
        options = {"host": "192.0.2.181", "protocol": "rtsp"}
    return make_hub("reolink", data=data, entities=ents, options=options)


@pytest.fixture
def fake_host(monkeypatch):
    FakeHost.instances = []
    monkeypatch.setattr(reolink, "Host", FakeHost)
    return FakeHost


async def test_reolink_setup_maps_unique_ids_to_channels(fake_host):
    hub, entry = reolink_hub()
    drv = reolink.ReolinkDriver(hub, entry)
    await drv.async_setup()
    host = fake_host.instances[0]
    assert host.address == "192.0.2.181"  # the options override beats ReoLink-Hub.local
    assert host.kw["bc_port"] == 9000 and host.kw["uid"] == HUB
    assert host.baichuan.subscribed and "homehub" in host.baichuan.callbacks
    assert drv.locate(hub.entities["binary_sensor.backyard_camera_1_motion"]) == (1, "motion")
    assert drv.locate(hub.entities["siren.reolink_hub_siren"]) == (None, "siren")
    s = hub.states
    assert s.get("binary_sensor.garage_camera_1_motion").state == "off"
    assert s.get("binary_sensor.garage_camera_1_person").state == "on"
    assert s.get("binary_sensor.backyard_camera_1_motion").state == "on"
    assert s.get("binary_sensor.backyard_camera_1_sleep_status").state == "on"
    assert s.get("sensor.garage_camera_1_battery").state == "87"
    assert s.get("light.garage_camera_1_status_led").state == "on"
    assert s.get("light.reolink_hub_status_led").state == "on"
    assert s.get("siren.reolink_hub_siren").attributes["supported_features"] == 51
    assert s.get("switch.garage_camera_1_infrared_lights_in_night_mode").state == "on"
    assert s.get("number.garage_camera_1_volume").state == "70"
    assert s.get("number.garage_camera_1_ai_person_sensitivity").state == "55"
    sel = s.get("select.garage_camera_1_day_night_mode")
    assert sel.state == "blackwhite" and "auto" in sel.attributes["options"]
    cam = s.get("camera.garage_camera_1_fluent")
    assert cam.state == "idle" and cam.attributes["entity_picture"].startswith(
        "/api/camera_proxy/camera.garage_camera_1_fluent?token=")


async def test_reolink_push_updates_binary_sensors(fake_host):
    hub, entry = reolink_hub()
    drv = reolink.ReolinkDriver(hub, entry)
    await drv.async_setup()
    host = fake_host.instances[0]
    host.motion[0] = True
    host.baichuan.callbacks["homehub"]()
    assert hub.states.get("binary_sensor.garage_camera_1_motion").state == "on"


async def test_reolink_poll_does_not_wake_battery_cameras_every_time(fake_host):
    hub, entry = reolink_hub()
    drv = reolink.ReolinkDriver(hub, entry)
    await drv.async_setup()
    host = fake_host.instances[0]
    await drv.async_poll()
    wake = [c for c in host.calls if c[0] == "get_states"][-1][1]
    assert wake == {0: False, 1: False}
    drv._last_wake -= reolink.BATTERY_WAKE_S + 1
    await drv.async_poll()
    assert [c for c in host.calls if c[0] == "get_states"][-1][1] == {0: True, 1: True}


async def test_reolink_offline_camera_is_unavailable(fake_host):
    hub, entry = reolink_hub()
    drv = reolink.ReolinkDriver(hub, entry)
    await drv.async_setup()
    fake_host.instances[0].online[1] = False
    await drv.async_poll()
    assert hub.states.get("binary_sensor.backyard_camera_1_motion").state == "unavailable"
    assert hub.states.get("binary_sensor.garage_camera_1_motion").state == "off"


async def test_reolink_services(fake_host):
    hub, entry = reolink_hub()
    drv = reolink.ReolinkDriver(hub, entry)
    await drv.async_setup()
    hub.drivers[entry.entry_id] = drv
    host = fake_host.instances[0]
    await hub.call_service("siren", "turn_on", {"entity_id": "siren.garage_camera_1_siren", "duration": 5})
    await hub.call_service("siren", "turn_on", {"entity_id": "siren.reolink_hub_siren"})
    await hub.call_service("siren", "turn_off", {"entity_id": "siren.garage_camera_1_siren"})
    await hub.call_service("light", "turn_off", {"entity_id": "light.garage_camera_1_status_led"})
    await hub.call_service("light", "turn_off", {"entity_id": "light.reolink_hub_status_led"})
    await hub.call_service("switch", "turn_off",
                           {"entity_id": "switch.garage_camera_1_infrared_lights_in_night_mode"})
    await hub.call_service("number", "set_value", {"entity_id": "number.garage_camera_1_volume", "value": 40})
    await hub.call_service("select", "select_option",
                           {"entity_id": "select.garage_camera_1_day_night_mode", "option": "auto"})
    assert ("set_siren", 0, True, 5) in host.calls
    assert ("set_siren", None, True, 2) in host.calls
    assert ("set_siren", 0, False, None) in host.calls
    assert ("set_status_led", 0, False) in host.calls
    assert ("set_state_light", False) in host.calls
    assert ("set_ir_lights", 0, False) in host.calls
    assert ("set_volume", 0, 40) in host.calls
    assert ("set_daynight", 0, "Auto") in host.calls
    assert hub.states.get("light.garage_camera_1_status_led").state == "off"
    assert hub.states.get("light.reolink_hub_status_led").state == "off"
    with pytest.raises(ServiceNotFound):
        await drv.async_call(hub.entities["binary_sensor.garage_camera_1_motion"], "binary_sensor", "turn_on", {})


async def test_reolink_camera_contract(fake_host):
    hub, entry = reolink_hub()
    drv = reolink.ReolinkDriver(hub, entry)
    await drv.async_setup()
    cam = hub.entities["camera.garage_camera_1_fluent"]
    assert await drv.async_camera_image(cam) == JPEG
    assert ("get_snapshot", 0, "sub") in fake_host.instances[0].calls
    assert await drv.async_camera_stream_url(cam) == "rtsp://hub/0/sub"
    with pytest.raises(ServiceNotFound):
        await drv.async_camera_image(hub.entities["siren.reolink_hub_siren"])


async def test_reolink_local_name_that_does_not_resolve_says_what_to_set(fake_host, monkeypatch):
    hub, entry = reolink_hub(options={})
    drv = reolink.ReolinkDriver(hub, entry)

    async def no_mdns(*a, **kw):
        raise OSError("nodename nor servname provided")

    import asyncio
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", no_mdns)
    with pytest.raises(RuntimeError, match="options\\['host'\\]"):
        await drv.async_setup()


async def test_reolink_unload_logs_out(fake_host):
    hub, entry = reolink_hub()
    drv = reolink.ReolinkDriver(hub, entry)
    await drv.async_setup()
    host = fake_host.instances[0]
    await drv.async_unload()
    assert ("logout",) in host.calls and not host.baichuan.subscribed


async def test_reolink_firmware_is_read_only_and_what_reolink_aio_cannot_do_is_disabled(fake_host, monkeypatch):
    from reolink_aio.software_version import NewSoftwareVersion

    hub, entry = reolink_hub()
    for eid, uid in (("update.reolink_hub_firmware", f"{HUB}_firmware"),
                     ("update.garage_camera_1_firmware", f"{HUB}_{CAM1}_firmware"),
                     ("button.garage_camera_1_pre_siren", f"{HUB}_{CAM1}_pre_siren"),
                     ("select.reolink_hub_scene_mode", f"{HUB}_scene_mode")):
        hub.entities[eid] = reolink.EntityEntry(entity_id=eid, unique_id=uid, platform="reolink",
                                                config_entry_id=entry.entry_id)
    checks = []

    async def check_new_firmware(self, ch_list=None):
        checks.append(ch_list)
        return {None: False, 0: NewSoftwareVersion("v3.1.0.9", release_notes="Fixes"), 1: False}

    monkeypatch.setattr(FakeHost, "check_new_firmware", check_new_firmware)
    drv = reolink.ReolinkDriver(hub, entry)
    await drv.async_setup()
    assert hub.entities["button.garage_camera_1_pre_siren"].disabled_by == "integration"
    assert hub.entities["select.reolink_hub_scene_mode"].disabled_by == "integration"
    assert hub.states.get("update.garage_camera_1_firmware").state == "unknown"  # not checked yet
    await drv.async_poll()
    await drv.async_poll()
    assert len(checks) == 1  # twice a day, not every poll
    cam = hub.states.get("update.garage_camera_1_firmware")
    assert cam.state == "on" and cam.attributes["latest_version"] == "v3.1.0.9"
    assert cam.attributes["installed_version"] == "v3.1.0.2" and cam.attributes["supported_features"] == 0
    hub_fw = hub.states.get("update.reolink_hub_firmware")
    assert hub_fw.state == "off" and hub_fw.attributes["latest_version"] == "v3.0.0.1"
    assert hub.states.get("button.garage_camera_1_pre_siren") is None
