"""Tasmota lights and web passwords in the driver, against a made-up device over real HTTP.

Every name, address and MAC is made up (tasmota_fakes.py).
"""
from __future__ import annotations

import pytest

from services.homehub.core import AuthRequired, ServiceNotFound
from services.homehub.drivers import tasmota
from services.homehub.drivers.tasmota import TasmotaDriver
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.tasmota_fakes import FakeTasmota

MAC = "02000000AA01"


def light_driver(host: str, *, password: str | None = None, light_index: int = 0):
    data = {"host": host, "mac": "02:00:00:00:aa:01"}
    if password:
        data.update({"username": "admin", "password": password})
    hub, entry = make_hub("tasmota", data, [{"entity_id": "light.shelf_bulb",
                                             "unique_id": f"{MAC}_light_light_{light_index}"}])
    drv = TasmotaDriver(hub, entry)
    hub.drivers[entry.entry_id] = drv
    return hub, drv


@pytest.fixture
async def device():
    made: list[FakeTasmota] = []

    async def make(kind: str, **kw) -> tuple[FakeTasmota, str]:
        dev = FakeTasmota(kind, **kw)
        made.append(dev)
        return dev, await dev.start()

    yield make
    for dev in made:
        await dev.stop()


# -- reading a light's type and state (pure) -----------------------------------------------------

def test_the_channel_count_is_the_light_type_with_has_color_modes():
    assert tasmota.color_modes({"POWER": "ON", "Dimmer": 50, "Channel": [50]}) == ["brightness"]
    assert tasmota.color_modes({"POWER": "ON", "Dimmer": 50, "CT": 300, "Channel": [20, 30]}) == ["color_temp"]
    assert tasmota.color_modes({"Dimmer": 50, "HSBColor": "0,100,50", "Channel": [50, 0, 0]}) == ["hs"]
    assert tasmota.color_modes({"Dimmer": 50, "HSBColor": "0,0,0", "White": 40,
                                "Channel": [0, 0, 0, 40]}) == ["hs", "white"]
    assert tasmota.color_modes({"Dimmer": 50, "HSBColor": "0,0,0", "CT": 200,
                                "Channel": [0, 0, 0, 20, 30]}) == ["color_temp", "hs"]
    # No Channel list (older firmware): the Color hex says how many channels there are.
    assert tasmota.color_modes({"Dimmer": 50, "Color": "1A2B", "CT": 250}) == ["color_temp"]
    assert tasmota.color_modes({"POWER": "ON"}) == ["onoff"]
    assert tasmota.has_light({"POWER": "ON", "Dimmer": 10}) and not tasmota.has_light({"POWER1": "ON"})
    # A light is numbered after the device's relays.
    assert tasmota.light_index({"POWER1": "ON", "POWER2": "OFF", "Dimmer": 5}) == 1


def test_attributes_are_has_with_brightness_kelvin_and_colour():
    ct = tasmota.light_attributes({"POWER": "ON", "Dimmer": 100, "CT": 250, "Channel": [60, 40]}, True)
    assert ct["color_mode"] == "color_temp" and ct["brightness"] == 255
    assert ct["color_temp"] == 250 and ct["color_temp_kelvin"] == 4000
    assert (ct["min_mireds"], ct["max_mireds"]) == (153, 500)
    assert (ct["min_color_temp_kelvin"], ct["max_color_temp_kelvin"]) == (2000, 6536)
    rgb = tasmota.light_attributes({"Dimmer": 50, "HSBColor": "240,100,50", "Channel": [0, 0, 50]}, True)
    assert rgb["color_mode"] == "hs" and rgb["hs_color"] == [240.0, 100.0] and rgb["rgb_color"] == [0, 0, 255]
    assert rgb["brightness"] == 128
    # RGB and white: only the white channels lit means it is showing white.
    cw = tasmota.light_attributes({"Dimmer": 40, "HSBColor": "0,0,0", "CT": 400,
                                   "Channel": [0, 0, 0, 10, 30]}, True)
    assert cw["color_mode"] == "color_temp" and cw["color_temp_kelvin"] == 2500
    off = tasmota.light_attributes({"Dimmer": 40, "CT": 400, "Channel": [10, 30]}, False)
    assert off["brightness"] is None and off["color_mode"] is None and off["supported_color_modes"] == ["color_temp"]


def test_turn_on_data_becomes_tasmota_commands_colour_first():
    both = ["color_temp", "hs"]
    assert tasmota.light_commands({}, both) == []
    assert tasmota.light_commands({"brightness": 128}, both) == ["Dimmer 50"]
    assert tasmota.light_commands({"brightness_pct": 0}, both) is None  # brightness 0 is off
    assert tasmota.light_commands({"color_temp_kelvin": 2700, "brightness_pct": 80}, both) == ["CT 370", "Dimmer 80"]
    assert tasmota.light_commands({"color_temp_kelvin": 10000}, both) == ["CT 153"]  # clamped to the range
    assert tasmota.light_commands({"rgb_color": [255, 0, 0]}, both) == ["HsbColor1 0", "HsbColor2 100"]
    assert tasmota.light_commands({"hs_color": [200, 50]}, ["color_temp"]) == []  # not a colour bulb
    assert tasmota.light_commands({"white": 255}, ["hs", "white"]) == ["White 100"]


# -- the driver against the made-up device -----------------------------------------------------------

async def test_a_dimmable_ct_bulb_is_a_light_with_brightness_and_kelvin(device):
    bulb, host = await device("ct")
    hub, drv = light_driver(host)
    await drv.async_setup()
    st = hub.states.get("light.shelf_bulb")
    assert st.state == "on" and st.attributes["supported_color_modes"] == ["color_temp"]
    assert st.attributes["brightness"] == 153 and st.attributes["color_temp"] == 300
    await hub.call_service("light", "turn_on", {"entity_id": "light.shelf_bulb", "color_temp_kelvin": 2500,
                                                "brightness_pct": 30})
    assert bulb.commands[-2:] == ["CT 400", "Dimmer 30"]
    st = hub.states.get("light.shelf_bulb")
    assert st.attributes["color_temp_kelvin"] == 2500 and st.attributes["brightness"] == 76
    await hub.call_service("light", "turn_off", {"entity_id": "light.shelf_bulb"})
    assert bulb.commands[-1] == "Power1 Off" and hub.states.get("light.shelf_bulb").state == "off"
    await hub.call_service("light", "turn_on", {"entity_id": "light.shelf_bulb"})
    assert bulb.commands[-1] == "Power1 On" and hub.states.get("light.shelf_bulb").state == "on"
    await hub.call_service("light", "toggle", {"entity_id": "light.shelf_bulb"})
    assert hub.states.get("light.shelf_bulb").state == "off"
    with pytest.raises(ServiceNotFound):
        await drv.async_call(hub.entities["light.shelf_bulb"], "light", "set_value", {})
    await drv.async_unload()


async def test_an_rgb_bulb_takes_a_colour_and_reports_it(device):
    bulb, host = await device("rgb")
    hub, drv = light_driver(host)
    await drv.async_setup()
    assert hub.states.get("light.shelf_bulb").attributes["supported_color_modes"] == ["hs"]
    await hub.call_service("light", "turn_on", {"entity_id": "light.shelf_bulb", "rgb_color": [0, 0, 255]})
    assert bulb.commands[-2:] == ["HsbColor1 240", "HsbColor2 100"]
    st = hub.states.get("light.shelf_bulb")
    assert st.attributes["color_mode"] == "hs" and st.attributes["hs_color"] == [240.0, 100.0]
    assert st.attributes["rgb_color"] == [0, 0, 255]
    await drv.async_unload()


async def test_an_rgbcw_bulb_switches_between_colour_and_white(device):
    bulb, host = await device("rgbcw")
    hub, drv = light_driver(host)
    await drv.async_setup()
    st = hub.states.get("light.shelf_bulb")
    assert st.attributes["supported_color_modes"] == ["color_temp", "hs"] and st.attributes["color_mode"] == "hs"
    await hub.call_service("light", "turn_on", {"entity_id": "light.shelf_bulb", "color_temp_kelvin": 4000})
    st = hub.states.get("light.shelf_bulb")
    assert bulb.commands[-1] == "CT 250" and st.attributes["color_mode"] == "color_temp"
    await drv.async_unload()


async def test_a_light_after_relays_uses_its_own_power_number(device):
    bulb, host = await device("ct")
    bulb.sts["POWER1"] = bulb.sts.pop("POWER")
    bulb.sts["POWER2"] = "ON"
    hub, drv = light_driver(host, light_index=1)
    await drv.async_setup()
    await hub.call_service("light", "turn_off", {"entity_id": "light.shelf_bulb"})
    assert bulb.commands[-1] == "Power2 Off"
    await drv.async_unload()


# -- web passwords ------------------------------------------------------------------------------------

async def test_a_locked_device_needs_its_password_sent_as_tasmotas_own_arguments(device):
    bulb, host = await device("ct", password="made-up-pw")
    _hub, drv = light_driver(host)
    with pytest.raises(AuthRequired, match="needs its web password"):
        await drv.async_setup()
    await drv.async_unload()
    _hub, drv = light_driver(host, password="wrong")
    with pytest.raises(AuthRequired, match="did not accept its web password"):
        await drv.async_setup()
    await drv.async_unload()
    hub, drv = light_driver(host, password="made-up-pw")
    await drv.async_setup()
    assert hub.states.get("light.shelf_bulb").state == "on"
    assert bulb.auth_seen[-1] == "made-up-pw"
    await drv.async_unload()


def test_no_error_text_carries_the_password():
    assert tasmota.cm_params("Status 0", None) == {"cmnd": "Status 0"}
    assert tasmota.cm_params("Power1 On", "pw") == {"user": "admin", "password": "pw", "cmnd": "Power1 On"}
    leaky = ValueError("bad URL http://192.0.2.9/cm?user=admin&password=s3cret&cmnd=Status+0")
    assert tasmota.safe_error(leaky, "s3cret") == "ValueError"
    assert tasmota.safe_error(TimeoutError(), "s3cret") == "TimeoutError"
    assert tasmota.safe_error(OSError("Connect call failed"), "s3cret") == "Connect call failed"


async def test_older_firmware_says_need_user_with_http_200():
    _hub, drv = light_driver("192.0.2.9")

    async def warning(command):
        raise drv._auth_failure()

    assert tasmota.needs_password(200, {"WARNING": "Need user=<username>&password=<password>"})
    assert tasmota.needs_password(401, None)
    assert not tasmota.needs_password(200, {"StatusSTS": {}})
    drv._command = warning  # type: ignore[method-assign]
    with pytest.raises(AuthRequired):
        await drv.async_setup()


async def test_a_password_changed_on_the_device_shows_on_the_entry_until_it_works_again(device):
    bulb, host = await device("ct", password="made-up-pw")
    hub, drv = light_driver(host, password="made-up-pw")
    await drv.async_setup()
    drv.entry.state = "loaded"
    bulb.password = "changed-on-the-device"
    await drv.async_poll()
    assert hub.states.get("light.shelf_bulb").state == "unavailable"
    assert drv.entry.state == "setup_retry" and drv.entry.reason.startswith("AuthRequired")
    bulb.password = "made-up-pw"
    await drv.async_poll()
    assert drv.entry.state == "loaded" and drv.entry.reason is None
    assert hub.states.get("light.shelf_bulb").state == "on"
    await drv.async_unload()
