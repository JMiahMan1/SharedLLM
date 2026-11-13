"""ESPHome driver against a stand-in APIClient: mapping, states, commands, actions, reconnects."""
from __future__ import annotations

import asyncio
import sys
from typing import ClassVar

import pytest
from aioesphomeapi import (
    APIConnectionError,
    APIVersion,
    BinarySensorInfo,
    BinarySensorState,
    ButtonInfo,
    ColorMode,
    CoverInfo,
    CoverOperation,
    CoverState,
    DeviceInfo,
    FanInfo,
    HomeassistantServiceCall,
    LightInfo,
    LightState,
    LockCommand,
    LockInfo,
    MediaPlayerCommand,
    MediaPlayerEntityState,
    MediaPlayerInfo,
    MediaPlayerState,
    NumberInfo,
    NumberState,
    SelectInfo,
    SelectState,
    SensorInfo,
    SensorState,
    SensorStateClass,
    SirenInfo,
    SwitchInfo,
    SwitchState,
    TextSensorInfo,
    TextSensorState,
    UserService,
    UserServiceArg,
    UserServiceArgType,
    ValveInfo,
)

from services.homehub.core import DeviceUnavailable, HubError, ServiceNotFound
from services.homehub.drivers import esphome
from services.homehub.drivers.esphome import ESPHomeDriver
from services.homehub.tests.conftest import make_hub

MAC = "02:00:00:00:01:02"
DATA = {"host": "192.0.2.127", "port": 6053, "password": "", "noise_psk": "not-a-real-key",
        "device_name": "smart-speaker"}

INFOS = [
    LightInfo(object_id="status_led_bar", key=1, name="Status LED Bar",
              supported_color_modes=[ColorMode.RGB, ColorMode.COLOR_TEMPERATURE],
              min_mireds=153, max_mireds=500, effects=["None", "Rainbow"]),
    SwitchInfo(object_id="mute", key=2, name="Mute"),
    SensorInfo(object_id="wifi_signal", key=3, name="WiFi Signal", unit_of_measurement="dBm",
               accuracy_decimals=0, device_class="signal_strength", state_class=SensorStateClass.MEASUREMENT),
    TextSensorInfo(object_id="ip", key=4, name="IP Address for smart-speaker"),
    NumberInfo(object_id="led_brightness", key=5, name="LED Brightness", min_value=0, max_value=100, step=1),
    SelectInfo(object_id="wake_word_sensitivity", key=6, name="Wake word sensitivity",
               options=["Slightly sensitive", "Moderately sensitive", "Very sensitive"]),
    ButtonInfo(object_id="reboot", key=7, name="Reboot"),
    MediaPlayerInfo(object_id="smart_speaker", key=8, name="smart-speaker", supports_pause=True,
                    feature_flags=1 | 4 | 8 | 512 | 4096 | 16384),
    SwitchInfo(object_id="scr_save_enable", key=9, name="Scr/save enable"),
    LightInfo(object_id="", key=10, name="", supported_color_modes=[ColorMode.BRIGHTNESS]),
    CoverInfo(object_id="garage", key=11, name="Garage Door", supports_stop=True, device_class="garage"),
    ValveInfo(object_id="water", key=12, name="Water Valve", supports_stop=False),
    BinarySensorInfo(object_id="presence", key=13, name="Presence detect", device_class="occupancy"),
    FanInfo(object_id="fan", key=14, name="Fan", supports_speed=True, supported_speed_count=4),
    LockInfo(object_id="lock", key=15, name="Lock", supports_open=False),
    SirenInfo(object_id="siren", key=16, name="Siren", tones=["beep"], supports_duration=True),
]

ENTITIES = [
    {"entity_id": "light.den_den_speaker_status_led_bar", "unique_id": f"{MAC}/0/light/Status LED Bar"},
    {"entity_id": "switch.den_den_speaker_mute", "unique_id": f"{MAC}/0/switch/Mute"},
    {"entity_id": "sensor.den_speaker_wifi_signal", "unique_id": f"{MAC}/0/sensor/WiFi Signal"},
    {"entity_id": "sensor.den_den_speaker_ip_address_for_smart_speaker",
     "unique_id": f"{MAC}/0/text_sensor/IP Address for smart-speaker"},
    {"entity_id": "number.den_den_speaker_led_brightness", "unique_id": f"{MAC}/0/number/LED Brightness"},
    {"entity_id": "select.den_den_speaker_wake_word_sensitivity",
     "unique_id": f"{MAC}/0/select/Wake word sensitivity"},
    {"entity_id": "button.den_den_speaker_reboot", "unique_id": f"{MAC}/0/button/Reboot"},
    {"entity_id": "media_player.den_den_speaker_smart_speaker",
     "unique_id": f"{MAC}/0/media_player/smart-speaker"},
    {"entity_id": "switch.scr_save", "unique_id": f"{MAC}/0/switch/Scr/save enable"},
    {"entity_id": "light.hall_lamp", "unique_id": f"{MAC}/0/light/"},
    {"entity_id": "cover.garage", "unique_id": f"{MAC}/0/cover/Garage Door"},
    {"entity_id": "valve.water", "unique_id": f"{MAC}/0/valve/Water Valve"},
    {"entity_id": "binary_sensor.presence", "unique_id": "smart-speakerbinary_sensorpresence"},  # older format
    {"entity_id": "fan.fan", "unique_id": f"{MAC}/0/fan/Fan"},
    {"entity_id": "lock.lock", "unique_id": f"{MAC}/0/lock/Lock"},
    {"entity_id": "siren.siren", "unique_id": f"{MAC}/0/siren/Siren"},
    {"entity_id": "sensor.gone", "unique_id": f"{MAC}/0/sensor/Removed From Firmware"},
    {"entity_id": "select.den_speaker_assistant", "unique_id": f"{MAC}-pipeline"},
]

SET_PLAYLISTS = UserService(name="set_playlists", key=99, args=[
    UserServiceArg(name="playlists", type=UserServiceArgType.STRING),
    UserServiceArg(name="shuffle", type=UserServiceArgType.BOOL),
    UserServiceArg(name="volumes", type=UserServiceArgType.INT_ARRAY),
])

INITIAL_STATES = [
    LightState(key=1, state=True, brightness=0.5, color_mode=ColorMode.RGB, red=1.0, green=0.0, blue=0.5,
               effect="None"),
    SwitchState(key=2, state=False),
    SensorState(key=3, state=-61.4),
    TextSensorState(key=4, state="192.0.2.127"),
    NumberState(key=5, state=40.0),
    SelectState(key=6, state="Moderately sensitive"),
    MediaPlayerEntityState(key=8, state=MediaPlayerState.IDLE, volume=0.5, muted=False),
    SwitchState(key=9, state=True),
    LightState(key=10, state=False),
    CoverState(key=11, position=0.0, current_operation=CoverOperation.IDLE),
    BinarySensorState(key=13, state=True),
]


class FakeClient:
    instances: ClassVar[list[FakeClient]] = []

    def __init__(self, fail: bool = False, host: str = "") -> None:
        self.fail = fail
        self.host = host
        self.api_version = APIVersion(1, 12)
        self.commands: list[tuple[str, tuple, dict]] = []
        self.sent_states: list[tuple[str, str | None, str]] = []
        self.executed: list[tuple[str, dict, object]] = []
        self.on_stop = None
        self.on_state = None
        FakeClient.instances.append(self)

    async def connect(self, on_stop=None, login=False):
        if self.fail:
            raise APIConnectionError("connection refused")
        self.on_stop = on_stop

    async def device_info_and_list_entities(self):
        return DeviceInfo(name="smart-speaker", mac_address=MAC), list(INFOS), [SET_PLAYLISTS]

    def subscribe_home_assistant_states_and_services(self, on_state, on_service_call, on_state_sub,
                                                     on_state_request=None):
        self.on_state, self.on_service_call = on_state, on_service_call
        self.on_state_sub, self.on_state_request = on_state_sub, on_state_request
        for s in INITIAL_STATES:
            on_state(s)

    async def disconnect(self, force=False):
        pass

    def send_home_assistant_state(self, entity_id, attribute, state):
        self.sent_states.append((entity_id, attribute, state))

    async def execute_service(self, service, data, *, return_response=None, timeout=30.0):
        self.executed.append((service.name, data, return_response))

    def __getattr__(self, name):
        if not name.endswith("_command"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.commands.append((name, args, kwargs))
            # Like a real node: a switch confirms its new state right away.
            if name == "switch_command":
                asyncio.get_running_loop().call_soon(self.on_state, SwitchState(key=args[0], state=args[1]))
        return record


@pytest.fixture
def driver(monkeypatch):
    FakeClient.instances.clear()
    hub, entry = make_hub("esphome", dict(DATA), ENTITIES, options={"allow_service_calls": True})
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host: FakeClient()  # type: ignore[method-assign]
    hub.drivers[entry.entry_id] = drv
    monkeypatch.setattr(esphome, "COMMAND_SETTLE", 0.05)
    return hub, drv


def last_command(drv):
    return drv._client.commands[-1]


async def test_setup_maps_unique_ids_and_publishes_pushed_states(driver):
    hub, drv = driver
    await drv.async_setup()
    led = hub.states.get("light.den_den_speaker_status_led_bar")
    assert led.state == "on"
    assert led.attributes["brightness"] == 128
    assert led.attributes["color_mode"] == "rgb" and led.attributes["rgb_color"] == [255, 0, 128]
    assert led.attributes["supported_color_modes"] == ["rgb", "color_temp"]
    assert led.attributes["min_color_temp_kelvin"] == 2000 and led.attributes["max_color_temp_kelvin"] == 6536
    assert led.attributes["effect_list"] == ["None", "Rainbow"]
    assert led.attributes["supported_features"] == 4 | 8 | 32
    wifi = hub.states.get("sensor.den_speaker_wifi_signal")
    assert wifi.state == "-61" and wifi.attributes["unit_of_measurement"] == "dBm"
    assert wifi.attributes["state_class"] == "measurement"
    assert hub.states.get("sensor.den_den_speaker_ip_address_for_smart_speaker").state == "192.0.2.127"
    num = hub.states.get("number.den_den_speaker_led_brightness")
    assert num.state == "40.0" and num.attributes["max"] == 100
    sel = hub.states.get("select.den_den_speaker_wake_word_sensitivity")
    assert sel.state == "Moderately sensitive" and "Very sensitive" in sel.attributes["options"]
    mp = hub.states.get("media_player.den_den_speaker_smart_speaker")
    assert mp.state == "idle" and mp.attributes["volume_level"] == 0.5
    assert mp.attributes["supported_features"] & 512
    assert hub.states.get("switch.scr_save").state == "on"           # a name with "/" in it
    assert hub.states.get("light.hall_lamp").state == "off"          # the node's nameless main entity
    assert hub.states.get("cover.garage").state == "closed"
    assert hub.states.get("binary_sensor.presence").state == "on"    # older unique_id format
    assert hub.states.get("button.den_den_speaker_reboot").state == "unknown"
    assert hub.states.get("sensor.gone").state == "unavailable"
    assert hub.states.get("select.den_speaker_assistant") is None   # HA-side, left alone


async def test_switch_command_waits_for_the_pushed_state(driver):
    hub, drv = driver
    await drv.async_setup()
    res = await hub.call_service("switch", "turn_on", {"entity_id": "switch.den_den_speaker_mute"})
    assert last_command(drv) == ("switch_command", (2, True), {"device_id": 0})
    assert hub.states.get("switch.den_den_speaker_mute").state == "on"
    assert any(s.entity_id == "switch.den_den_speaker_mute" for s in res["changed_states"])
    await hub.call_service("switch", "toggle", {"entity_id": "switch.den_den_speaker_mute"})
    assert last_command(drv)[1] == (2, False)


async def test_light_commands(driver):
    hub, drv = driver
    await drv.async_setup()
    eid = "light.den_den_speaker_status_led_bar"
    await hub.call_service("light", "turn_on", {"entity_id": eid, "brightness": 51, "rgb_color": [0, 128, 64]})
    name, args, kw = last_command(drv)
    assert name == "light_command" and args == (1,)
    assert kw["state"] is True and kw["brightness"] == pytest.approx(0.2)
    assert kw["color_mode"] == ColorMode.RGB
    assert kw["rgb"] == pytest.approx((0.0, 1.0, 0.5)) and kw["color_brightness"] == pytest.approx(128 / 255)
    await hub.call_service("light", "turn_on", {"entity_id": eid, "color_temp_kelvin": 2700, "transition": 2})
    kw = last_command(drv)[2]
    assert kw["color_mode"] == ColorMode.COLOR_TEMPERATURE
    assert kw["color_temperature"] == pytest.approx(1_000_000 / 2700) and kw["transition_length"] == 2.0
    await hub.call_service("light", "turn_on", {"entity_id": eid, "effect": "Rainbow"})
    assert last_command(drv)[2]["effect"] == "Rainbow"
    await hub.call_service("light", "turn_off", {"entity_id": eid})
    assert last_command(drv)[2] == {"device_id": 0, "state": False}
    await hub.call_service("light", "toggle", {"entity_id": "light.hall_lamp"})
    assert last_command(drv)[1] == (10,) and last_command(drv)[2]["state"] is True


async def test_number_select_button_cover_valve_fan_lock_siren(driver):
    hub, drv = driver
    await drv.async_setup()
    await hub.call_service("number", "set_value", {"entity_id": "number.den_den_speaker_led_brightness",
                                                   "value": "75"})
    assert last_command(drv) == ("number_command", (5, 75.0), {"device_id": 0})
    await hub.call_service("select", "select_option", {"entity_id": "select.den_den_speaker_wake_word_sensitivity",
                                                       "option": "Very sensitive"})
    assert last_command(drv) == ("select_command", (6, "Very sensitive"), {"device_id": 0})
    with pytest.raises(HubError):
        await hub.call_service("select", "select_option",
                               {"entity_id": "select.den_den_speaker_wake_word_sensitivity", "option": "Loud"})
    await hub.call_service("button", "press", {"entity_id": "button.den_den_speaker_reboot"})
    assert last_command(drv) == ("button_command", (7,), {"device_id": 0})
    assert hub.states.get("button.den_den_speaker_reboot").state not in ("unknown", "unavailable")
    await hub.call_service("cover", "open_cover", {"entity_id": "cover.garage"})
    assert last_command(drv) == ("cover_command", (11,), {"position": 1.0, "device_id": 0})
    await hub.call_service("cover", "stop_cover", {"entity_id": "cover.garage"})
    assert last_command(drv)[2] == {"stop": True, "device_id": 0}
    await hub.call_service("valve", "close_valve", {"entity_id": "valve.water"})
    assert last_command(drv) == ("valve_command", (12,), {"position": 0.0, "device_id": 0})
    await hub.call_service("fan", "set_percentage", {"entity_id": "fan.fan", "percentage": 60})
    assert last_command(drv) == ("fan_command", (14,), {"device_id": 0, "state": True, "speed_level": 3})
    await hub.call_service("lock", "unlock", {"entity_id": "lock.lock"})
    assert last_command(drv)[1] == (15, LockCommand.UNLOCK)
    await hub.call_service("siren", "turn_on", {"entity_id": "siren.siren", "tone": "beep", "duration": 3})
    assert last_command(drv)[2]["tone"] == "beep" and last_command(drv)[2]["duration"] == 3
    with pytest.raises(ServiceNotFound):
        await drv.async_call(hub.entities["sensor.den_speaker_wifi_signal"], "sensor", "set_value", {"value": 1})


async def test_media_player_commands(driver):
    hub, drv = driver
    await drv.async_setup()
    mp = "media_player.den_den_speaker_smart_speaker"
    await hub.call_service("media_player", "play_media", {"entity_id": mp, "media_content_id": "http://x/a.mp3",
                                                          "media_content_type": "music", "announce": True})
    assert last_command(drv) == ("media_player_command", (8,),
                                 {"media_url": "http://x/a.mp3", "announcement": True, "device_id": 0})
    await hub.call_service("media_player", "volume_set", {"entity_id": mp, "volume_level": 0.3})
    assert last_command(drv)[2] == {"volume": 0.3, "device_id": 0}
    await hub.call_service("media_player", "media_pause", {"entity_id": mp})
    assert last_command(drv)[2]["command"] == MediaPlayerCommand.PAUSE
    await hub.call_service("media_player", "volume_mute", {"entity_id": mp, "is_volume_muted": True})
    assert last_command(drv)[2]["command"] == MediaPlayerCommand.MUTE


async def test_user_defined_actions_become_hub_services(driver):
    hub, drv = driver
    await drv.async_setup()
    assert ("esphome", "smart_speaker_set_playlists") in hub.services
    await hub.call_service("esphome", "smart_speaker_set_playlists",
                           {"playlists": "Worship,Kids", "shuffle": "true", "volumes": ["3", 4.0]})
    name, data, rr = drv._client.executed[-1]
    assert name == "set_playlists" and rr is None
    assert data == {"playlists": "Worship,Kids", "shuffle": True, "volumes": [3, 4]}
    with pytest.raises(HubError, match="needs 'shuffle'"):
        await hub.call_service("esphome", "smart_speaker_set_playlists", {"playlists": "x", "volumes": []})
    await drv.async_unload()
    assert ("esphome", "smart_speaker_set_playlists") not in hub.services


async def test_disconnect_marks_unavailable_and_reconnects(driver, monkeypatch, caplog):
    monkeypatch.setattr(esphome, "RECONNECT_DELAYS", (0,))
    hub, drv = driver
    await drv.async_setup()
    first = drv._client
    await first.on_stop(False)
    assert hub.states.get("switch.den_den_speaker_mute").state == "unavailable"
    assert "disconnected" in caplog.text
    with pytest.raises(DeviceUnavailable):
        await hub.call_service("switch", "turn_on", {"entity_id": "switch.den_den_speaker_mute"})
    await drv._reconnect_task
    assert drv._client is not None and drv._client is not first
    assert hub.states.get("switch.den_den_speaker_mute").state == "off"
    await drv.async_unload()


async def test_reconnect_keeps_trying_and_poll_restarts_a_stopped_loop(driver, monkeypatch):
    monkeypatch.setattr(esphome, "RECONNECT_DELAYS", (0,))
    _hub, drv = driver
    await drv.async_setup()
    attempts = {"n": 0}

    def flaky(host):
        attempts["n"] += 1
        return FakeClient(fail=attempts["n"] < 3)

    drv._make_client = flaky  # type: ignore[method-assign]
    await drv._client.on_stop(False)
    await asyncio.wait_for(drv._reconnect_task, 2)
    assert attempts["n"] == 3 and drv.connected
    # A watchdog poll with a live connection does nothing; with none it restarts the loop.
    await drv.async_poll()
    drv._client = None
    await drv.async_poll()
    assert drv._reconnect_task is not None
    await asyncio.wait_for(drv._reconnect_task, 2)
    assert drv.connected
    await drv.async_unload()


async def test_setup_raises_when_the_node_is_unreachable(driver):
    _hub, drv = driver
    drv._make_client = lambda host: FakeClient(fail=True)  # type: ignore[method-assign]
    with pytest.raises(DeviceUnavailable, match="unreachable"):
        await drv.async_setup()
    assert drv._unsub_bus is None


async def test_node_actions_run_on_the_hub_only_when_allowed(driver):
    hub, drv = driver
    await drv.async_setup()
    seen = []

    async def handler(_hub, call):
        seen.append((call.domain, call.service, call.data))

    hub.register_service("script", "announce", handler)
    drv._client.on_service_call(HomeassistantServiceCall(service="script.announce", data={"message": "hi"}))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert seen == [("script", "announce", {"message": "hi"})]
    drv.allow_service_calls = False
    drv._client.on_service_call(HomeassistantServiceCall(service="script.announce", data={"message": "no"}))
    await asyncio.sleep(0)
    assert len(seen) == 1


async def test_node_subscriptions_to_hub_states(driver):
    hub, drv = driver
    await drv.async_setup()
    hub.publish("sensor.outside_temperature", "21.5", {"unit_of_measurement": "°C", "stale": True})
    drv._client.on_state_sub("sensor.outside_temperature", None)
    drv._client.on_state_sub("sensor.outside_temperature", "stale")
    assert drv._client.sent_states == [("sensor.outside_temperature", None, "21.5"),
                                       ("sensor.outside_temperature", "stale", "on")]
    hub.publish("sensor.outside_temperature", "22.0", {"unit_of_measurement": "°C", "stale": False})
    assert ("sensor.outside_temperature", None, "22.0") in drv._client.sent_states
    assert ("sensor.outside_temperature", "stale", "off") in drv._client.sent_states


async def test_voice_pipeline_selects_are_kept_by_the_hub(driver):
    hub, drv = driver
    hub.publish("select.den_speaker_assistant", "preferred", {"options": ["preferred", "Jarvis"]})
    await drv.async_setup()
    await hub.call_service("select", "select_option", {"entity_id": "select.den_speaker_assistant",
                                                       "option": "Jarvis"})
    st = hub.states.get("select.den_speaker_assistant")
    assert st.state == "Jarvis" and st.attributes["options"] == ["preferred", "Jarvis"]


def test_ha_color_mode_names():
    from services.homehub.drivers.esphome import ha_color_mode
    assert ha_color_mode(ColorMode.ON_OFF) == "onoff"
    assert ha_color_mode(ColorMode.BRIGHTNESS) == "brightness"
    assert ha_color_mode(ColorMode.WHITE) == "white"
    assert ha_color_mode(ColorMode.COLD_WARM_WHITE) == "color_temp"
    assert ha_color_mode(ColorMode.RGB) == "rgb"
    assert ha_color_mode(ColorMode.RGB_WHITE) == "rgbw"
    assert ha_color_mode(ColorMode.RGB_COLD_WARM_WHITE) == "rgbww"


def _driver_with(options=None, data=None):
    hub, entry = make_hub("esphome", {**DATA, **(data or {})}, ENTITIES, options=options or {})
    drv = ESPHomeDriver(hub, entry)
    hub.drivers[entry.entry_id] = drv
    return hub, drv


async def test_stale_ip_falls_back_to_mdns_and_keeps_it(caplog, monkeypatch):
    monkeypatch.setattr(esphome, "RECONNECT_DELAYS", (0,))
    hub, drv = _driver_with()
    tried: list[str] = []

    def factory(host):
        tried.append(host)
        return FakeClient(fail=host != "smart-speaker.local", host=host)

    drv._make_client = factory  # type: ignore[method-assign]
    await drv.async_setup()
    assert tried == ["192.0.2.127", "smart-speaker.local"]
    assert drv.host == "smart-speaker.local"
    assert "answered at smart-speaker.local, not 192.0.2.127" in caplog.text
    assert hub.states.get("switch.den_den_speaker_mute").state == "off"
    # A reconnect goes straight to the address that worked.
    tried.clear()
    await drv._client.on_stop(False)
    await asyncio.wait_for(drv._reconnect_task, 2)
    assert tried == ["smart-speaker.local"]
    await drv.async_unload()


async def test_both_addresses_failing_names_both():
    _, drv = _driver_with()
    drv._make_client = lambda host: FakeClient(fail=True, host=host)  # type: ignore[method-assign]
    with pytest.raises(DeviceUnavailable) as err:
        await drv.async_setup()
    assert "192.0.2.127" in str(err.value) and "smart-speaker.local" in str(err.value)


async def test_options_host_overrides_everything():
    _, drv = _driver_with(options={"host": "192.0.2.250"})
    tried: list[str] = []

    def factory(host):
        tried.append(host)
        return FakeClient(fail=True, host=host)

    drv._make_client = factory  # type: ignore[method-assign]
    with pytest.raises(DeviceUnavailable):
        await drv.async_setup()
    assert tried == ["192.0.2.250"]


def test_no_mdns_fallback_without_a_device_name():
    _, drv = _driver_with(data={"device_name": ""})
    assert drv.candidates == ["192.0.2.127"]


async def test_camera_snapshot_comes_from_a_single_image_request(monkeypatch):
    from aioesphomeapi import CameraInfo, CameraState

    cam_info = CameraInfo(object_id="garage_camera", key=50, name="Garage Camera")
    monkeypatch.setattr(sys.modules[__name__], "INFOS", [*INFOS, cam_info])

    class CamClient(FakeClient):
        def request_single_image(self):
            asyncio.get_running_loop().call_soon(self.on_state, CameraState(key=50, data=b"\xff\xd8jpeg\xff\xd9"))

    hub, entry = make_hub("esphome", dict(DATA), [*ENTITIES, {"entity_id": "camera.garage_door_garage_camera",
                                                               "unique_id": f"{MAC}/0/camera/Garage Camera"}])
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host: CamClient()  # type: ignore[method-assign]
    await drv.async_setup()
    ent = hub.entities["camera.garage_door_garage_camera"]
    st = hub.states.get(ent.entity_id)
    assert st.state == "idle" and st.attributes["entity_picture"].startswith("/api/camera_proxy/")
    assert await drv.async_camera_image(ent) == b"\xff\xd8jpeg\xff\xd9"
    assert st.attributes["access_token"] in drv.access_tokens(ent.entity_id)
    await drv.async_unload()


async def test_live_video_streams_over_the_api_and_stops_when_the_viewer_leaves(monkeypatch):
    """No camera web page: frames come over the node's own encrypted API, re-asked while someone watches."""
    from aioesphomeapi import CameraInfo, CameraState

    monkeypatch.setattr(sys.modules[__name__], "INFOS",
                        [*INFOS, CameraInfo(object_id="garage_camera", key=50, name="Garage Camera")])
    monkeypatch.setattr(esphome, "CAMERA_STREAM_RENEW_S", 0.0)  # ask again before every frame
    asks: list[str] = []

    class Streamer(FakeClient):
        def request_image_stream(self):
            asks.append("stream")
            n = len(asks)
            asyncio.get_running_loop().call_soon(
                self.on_state, CameraState(key=50, data=b"\xff\xd8frame%d\xff\xd9" % n))

    hub, entry = make_hub("esphome", dict(DATA), [*ENTITIES, {"entity_id": "camera.garage_door_garage_camera",
                                                               "unique_id": f"{MAC}/0/camera/Garage Camera"}])
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host: Streamer()  # type: ignore[method-assign]
    await drv.async_setup()
    ctype, body = await drv.async_open_mjpeg_stream(hub.entities["camera.garage_door_garage_camera"])
    assert ctype.startswith("multipart/x-mixed-replace; boundary=")
    parts = [await body.__anext__() for _ in range(3)]
    assert b"Content-Type: image/jpeg" in parts[0] and b"\xff\xd8frame1\xff\xd9" in parts[0]
    assert b"frame3" in parts[2] and len(asks) == 3
    assert drv._frame_queues  # watching
    await body.aclose()
    assert not drv._frame_queues  # the viewer left: nothing keeps the camera streaming
    await drv.async_unload()


async def test_live_video_ends_when_the_camera_goes_quiet(monkeypatch):
    from aioesphomeapi import CameraInfo

    monkeypatch.setattr(sys.modules[__name__], "INFOS",
                        [*INFOS, CameraInfo(object_id="garage_camera", key=50, name="Garage Camera")])
    monkeypatch.setattr(esphome, "CAMERA_TIMEOUT_S", 0.05)

    class Quiet(FakeClient):
        def request_image_stream(self):
            pass

    hub, entry = make_hub("esphome", dict(DATA), [*ENTITIES, {"entity_id": "camera.garage_door_garage_camera",
                                                               "unique_id": f"{MAC}/0/camera/Garage Camera"}])
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host: Quiet()  # type: ignore[method-assign]
    await drv.async_setup()
    _ctype, body = await drv.async_open_mjpeg_stream(hub.entities["camera.garage_door_garage_camera"])
    assert [chunk async for chunk in body] == []
    assert not drv._frame_queues
    await drv.async_unload()


async def test_camera_snapshot_times_out_cleanly(monkeypatch):
    from aioesphomeapi import CameraInfo

    monkeypatch.setattr(sys.modules[__name__], "INFOS",
                        [*INFOS, CameraInfo(object_id="garage_camera", key=50, name="Garage Camera")])
    monkeypatch.setattr(esphome, "CAMERA_TIMEOUT_S", 0.05)

    class Silent(FakeClient):
        def request_single_image(self):
            pass

    hub, entry = make_hub("esphome", dict(DATA), [*ENTITIES, {"entity_id": "camera.garage_door_garage_camera",
                                                               "unique_id": f"{MAC}/0/camera/Garage Camera"}])
    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host: Silent()  # type: ignore[method-assign]
    await drv.async_setup()
    with pytest.raises(DeviceUnavailable):
        await drv.async_camera_image(hub.entities["camera.garage_door_garage_camera"])
    assert not drv._waiters.get((0, 50))
    await drv.async_unload()


async def test_assist_satellite_announce_speaks_on_the_node(driver, monkeypatch):
    """assist_satellite.announce (Jarvis's intercom) plays Jarvis's voice through the node's announcement call."""
    from services.homehub import tts
    from services.homehub.core import EntityEntry

    hub, drv = driver
    sat = EntityEntry(entity_id="assist_satellite.den_speaker", platform="esphome", unique_id=f"{MAC}-assist_satellite",
                      config_entry_id=drv.entry.entry_id, device_id="dev1")
    hub.entities[sat.entity_id] = sat
    await drv.async_setup()
    announced = []

    async def announce(media_id, timeout, text="", preannounce_media_id="", start_conversation=False):
        announced.append((media_id, text, preannounce_media_id))

    drv._client.send_voice_assistant_announcement_await_response = announce

    async def clip(message, voice=None):
        return f"http://hub.example.com:8123/api/tts_proxy/x.wav#{message}"

    monkeypatch.setattr(tts, "make_clip", clip)
    await hub.call_service("assist_satellite", "announce", {"entity_id": sat.entity_id, "message": "Dinner time"})
    assert announced == [("http://hub.example.com:8123/api/tts_proxy/x.wav#Dinner time", "Dinner time", "")]
    await hub.call_service("assist_satellite", "announce", {"entity_id": sat.entity_id,
                                                            "media_id": "http://jarvis.example.com/a.wav"})
    assert announced[-1][0] == "http://jarvis.example.com/a.wav"
    with pytest.raises(ServiceNotFound, match="voice pipeline"):
        await hub.call_service("assist_satellite", "start_conversation", {"entity_id": sat.entity_id,
                                                                          "start_message": "Hi"})
    with pytest.raises(HubError, match="message or a media_id"):
        await hub.call_service("assist_satellite", "announce", {"entity_id": sat.entity_id})
