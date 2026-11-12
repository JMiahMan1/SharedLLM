"""Roborock driver against fake python-roborock devices (no network)."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar

import pytest

from services.homehub.core import HubError
from services.homehub.drivers import roborock as rr
from services.homehub.drivers.roborock import RoborockDriver, activity

DUID = "jjaZEMhLagWnMPW9tRxzg"   # the device's real casing
LDUID = DUID.lower()               # HA lower-cases it in unique_ids


class Trait(SimpleNamespace):
    async def refresh(self):
        self.refreshes = getattr(self, "refreshes", 0) + 1
        if getattr(self, "fail", False):
            raise TimeoutError("no answer")


class Status(Trait):
    fan_speed_mapping: ClassVar[dict[int, str]] = {101: "quiet", 102: "balanced", 103: "turbo", 104: "max", 105: "gentle"}
    water_mode_mapping: ClassVar[dict[int, str]] = {200: "off", 201: "low", 202: "medium", 203: "high"}
    mop_route_mapping: ClassVar[dict[int, str]] = {300: "standard", 301: "deep"}

    def add_update_listener(self, cb):
        self.listener = cb
        return lambda: None

    @property
    def fan_speed_name(self):
        return self.fan_speed_mapping.get(self.fan_power)

    @property
    def water_mode_name(self):
        return self.water_mode_mapping.get(self.water_box_mode)

    @property
    def mop_route_name(self):
        return self.mop_route_mapping.get(self.mop_mode)

    @property
    def is_battery_charging(self):
        return self.state_name == "charging"

    async def set_cleaning_mode(self, mode):
        self.current_cleaning_mode_name = mode


class Command:
    def __init__(self):
        self.sent = []

    async def send(self, command, params=None):
        self.sent.append((command, params))
        return "ok"


class Volume(Trait):
    async def set_volume(self, v):
        self.volume = v


class Dnd(Trait):
    async def enable(self):
        self.enabled = 1

    async def disable(self):
        self.enabled = 0


def make_props():
    return SimpleNamespace(
        status=Status(state_name="charging", battery=100, clean_time=5967, square_meter_clean_area=21.8,
                      error_code_name="none", fan_power=104, water_box_mode=202, mop_mode=300,
                      water_box_status=1, water_box_carriage_status=0, water_shortage_status=0,
                      in_cleaning=0, current_cleaning_mode_name="vacuum"),
        clean_summary=Trait(clean_time=360000, square_meter_clean_area=1234.5, clean_count=88,
                            last_clean_record=SimpleNamespace(
                                begin_datetime=datetime(2026, 11, 11, 6, 27, 49, tzinfo=UTC),
                                end_datetime=datetime(2026, 11, 11, 8, 7, 16, tzinfo=UTC))),
        consumables=Trait(main_brush_time_left=1054721, side_brush_time_left=500000,
                          filter_time_left=400000, sensor_time_left=100000),
        sound_volume=Volume(volume=60),
        dnd=Dnd(enabled=1, start_hour=22, start_minute=0, end_hour=8, end_minute=30),
        maps=Trait(current_map_info=SimpleNamespace(name="Map 0", map_flag=0), current_map=0,
                   map_info=[SimpleNamespace(name="Map 0", map_flag=0)]),
        command=Command(),
    )


class FakeManager:
    def __init__(self, props):
        self.device = SimpleNamespace(duid=DUID, v1_properties=props)
        self.closed = False

    async def get_devices(self):
        return [self.device]

    async def close(self):
        self.closed = True


def ent(entity_id, key, **kw):
    uid = LDUID if key == "" else f"{key}_{LDUID}"
    return {"entity_id": entity_id, "unique_id": uid, **kw}


ENTITIES = [
    ent("vacuum.roborock_s5_max", "", supported_features=30524,
        capabilities={"fan_speed_list": ["quiet", "balanced", "turbo", "max", "gentle"]}),
    ent("sensor.roborock_s5_max_status", "status", capabilities={"options": ["charging", "cleaning"]}),
    ent("sensor.roborock_s5_max_battery", "battery", unit_of_measurement="%"),
    ent("sensor.roborock_s5_max_cleaning_time", "cleaning_time", unit_of_measurement="s"),
    ent("sensor.roborock_s5_max_cleaning_area", "cleaning_area", unit_of_measurement="m²"),
    ent("sensor.roborock_s5_max_total_cleaning_count", "total_cleaning_count",
        capabilities={"state_class": "total_increasing"}),
    ent("sensor.roborock_s5_max_main_brush_time_left", "main_brush_time_left", unit_of_measurement="s"),
    ent("sensor.roborock_s5_max_last_clean_begin", "last_clean_start"),
    ent("sensor.roborock_s5_max_vacuum_error", "vacuum_error"),
    ent("sensor.roborock_s5_max_current_room", "current_room"),
    ent("binary_sensor.roborock_s5_max_mop_attached", "water_box_carriage_status"),
    ent("binary_sensor.roborock_s5_max_water_box_attached", "water_box_status"),
    ent("binary_sensor.roborock_s5_max_charging", "battery_charging"),
    ent("binary_sensor.living_room_s5_max_water_shortage", "water_shortage"),
    ent("select.roborock_s5_max_mop_intensity", "water_box_mode",
        capabilities={"options": ["off", "low", "medium", "high", "custom_water_flow"]}),
    ent("select.s5_max_mop_mode", "mop_mode", capabilities={"options": ["standard", "deep"]}),
    ent("select.roborock_s5_max_selected_map", "selected_map", capabilities={"options": ["Map 0"]}),
    ent("number.roborock_s5_max_volume", "volume", capabilities={"min": 0, "max": 100, "step": 1.0}),
    ent("switch.roborock_s5_max_do_not_disturb", "dnd_switch"),
    ent("time.roborock_s5_max_do_not_disturb_begin", "dnd_start_time"),
    {"entity_id": "image.roborock_s5_max_map_0", "unique_id": f"{LDUID}_map_Map 0"},
]

DATA = {"base_url": "https://usiot.roborock.com", "username": "someone@example.com",
        "user_data": {"rriot": {"u": "u", "s": "s", "h": "h", "k": "k", "r": {"r": "US", "a": "", "m": "", "l": ""}},
                      "token": "t", "rruid": "r"}}


async def setup(props=None):
    from services.homehub.tests.conftest import make_hub
    hub, entry = make_hub("roborock", DATA, ENTITIES, options={"drawables": {}})
    props = props or make_props()
    manager = FakeManager(props)
    driver = RoborockDriver(hub, entry)

    async def create():
        return manager

    driver._create_manager = create
    await driver.async_setup()
    hub.drivers[entry.entry_id] = driver
    return hub, driver, props, manager


def test_activity_mapping():
    assert activity("charging") == "docked"
    assert activity("cleaning") == "cleaning"
    assert activity("segment_cleaning") == "cleaning"
    assert activity("returning_home") == "returning"
    assert activity("paused") == "paused"
    assert activity("error") == "error"
    assert activity("idle") == "idle"
    assert activity("zoned_clean_mop_mopping") == "cleaning"
    assert activity(None) == "unknown"


async def test_states_published_like_ha():
    hub, _driver, _props, _ = await setup()
    v = hub.states.get("vacuum.roborock_s5_max")
    assert v.state == "docked"
    assert v.attributes["fan_speed"] == "max" and v.attributes["battery_level"] == 100
    assert v.attributes["fan_speed_list"][0] == "quiet"
    assert v.attributes["supported_features"] == 30524
    def s(e):
        return hub.states.get(e).state

    assert s("sensor.roborock_s5_max_status") == "charging"
    # Options, ranges and features are the device's and the driver's own, not the imported registry's.
    assert {"charging", "cleaning"} <= set(hub.states.get("sensor.roborock_s5_max_status").attributes["options"])
    assert hub.states.get("select.roborock_s5_max_mop_intensity").attributes["options"] == [
        "off", "low", "medium", "high"]
    assert hub.states.get("sensor.roborock_s5_max_battery").attributes["unit_of_measurement"] == "%"
    assert s("sensor.roborock_s5_max_battery") == "100"
    assert s("sensor.roborock_s5_max_cleaning_time") == "5967"
    assert s("sensor.roborock_s5_max_cleaning_area") == "21.8"
    assert s("sensor.roborock_s5_max_total_cleaning_count") == "88"
    assert s("sensor.roborock_s5_max_main_brush_time_left") == "1054721"
    assert s("sensor.roborock_s5_max_last_clean_begin") == "2026-11-11T06:27:49+00:00"
    assert s("sensor.roborock_s5_max_vacuum_error") == "none"
    assert s("sensor.roborock_s5_max_current_room") == "unknown"
    assert s("binary_sensor.roborock_s5_max_mop_attached") == "off"
    assert s("binary_sensor.roborock_s5_max_water_box_attached") == "on"
    assert s("binary_sensor.roborock_s5_max_charging") == "on"
    assert s("binary_sensor.living_room_s5_max_water_shortage") == "off"
    assert s("select.roborock_s5_max_mop_intensity") == "medium"
    assert s("select.s5_max_mop_mode") == "standard"
    assert s("select.roborock_s5_max_selected_map") == "Map 0"
    assert s("number.roborock_s5_max_volume") == "60"
    assert s("switch.roborock_s5_max_do_not_disturb") == "on"
    assert s("time.roborock_s5_max_do_not_disturb_begin") == "22:00:00"
    assert s("image.roborock_s5_max_map_0") == "unavailable"


@pytest.mark.parametrize("service,command", [
    ("start", "app_start"), ("pause", "app_pause"), ("stop", "app_stop"),
    ("return_to_base", "app_charge"), ("locate", "find_me"), ("clean_spot", "app_spot"),
])
async def test_vacuum_commands(service, command):
    hub, _driver, props, _ = await setup()
    await hub.call_service("vacuum", service, {"entity_id": "vacuum.roborock_s5_max"})
    assert props.command.sent[-1] == (command, None)
    assert props.status.refreshes >= 2  # read back after the command


async def test_fan_speed_and_send_command():
    hub, _driver, props, _ = await setup()
    await hub.call_service("vacuum", "set_fan_speed", {"entity_id": "vacuum.roborock_s5_max", "fan_speed": "turbo"})
    assert props.command.sent[-1] == ("set_custom_mode", [103])
    with pytest.raises(HubError):
        await hub.call_service("vacuum", "set_fan_speed", {"entity_id": "vacuum.roborock_s5_max", "fan_speed": "warp"})
    await hub.call_service("vacuum", "send_command", {"entity_id": "vacuum.roborock_s5_max",
                                                      "command": "app_segment_clean", "params": [16, 17]})
    assert props.command.sent[-1] == ("app_segment_clean", [16, 17])


async def test_config_entities():
    hub, _driver, props, _ = await setup()
    await hub.call_service("select", "select_option",
                           {"entity_id": "select.roborock_s5_max_mop_intensity", "option": "high"})
    assert props.command.sent[-1] == ("set_water_box_custom_mode", [203])
    await hub.call_service("select", "select_option", {"entity_id": "select.s5_max_mop_mode", "option": "deep"})
    assert props.command.sent[-1] == ("set_mop_mode", [301])
    await hub.call_service("number", "set_value", {"entity_id": "number.roborock_s5_max_volume", "value": 40})
    assert hub.states.get("number.roborock_s5_max_volume").state == "40"
    await hub.call_service("switch", "turn_off", {"entity_id": "switch.roborock_s5_max_do_not_disturb"})
    assert hub.states.get("switch.roborock_s5_max_do_not_disturb").state == "off"


async def test_push_update_republishes():
    hub, _driver, props, _ = await setup()
    props.status.state_name = "cleaning"
    props.status.listener()
    assert hub.states.get("vacuum.roborock_s5_max").state == "cleaning"


async def test_unanswered_status_marks_unavailable_and_recovers():
    hub, driver, props, _ = await setup()
    props.status.fail = True
    await driver.async_poll()
    assert hub.states.get("vacuum.roborock_s5_max").state == "unavailable"
    assert hub.states.get("sensor.roborock_s5_max_battery").state == "unavailable"
    props.status.fail = False
    await driver.async_poll()
    assert hub.states.get("vacuum.roborock_s5_max").state == "docked"


async def test_slow_traits_only_every_few_minutes():
    _hub, driver, props, _ = await setup()
    before = props.consumables.refreshes
    await driver.async_poll()
    assert props.consumables.refreshes == before
    driver._slow_at[LDUID] = 0
    await driver.async_poll()
    assert props.consumables.refreshes == before + 1


async def test_login_failure_is_raised_for_retry_and_logged(caplog):
    from services.homehub.tests.conftest import make_hub
    hub, entry = make_hub("roborock", DATA, ENTITIES)
    driver = RoborockDriver(hub, entry)

    async def create():
        raise rr.RoborockException("token expired")

    driver._create_manager = create
    with pytest.raises(rr.RoborockException):
        await driver.async_setup()
    assert "login with the stored token failed" in caplog.text


async def test_real_library_builds_params_from_ha_user_data(monkeypatch):
    """The stored ``user_data`` dict goes through UserData.from_dict unchanged."""
    if rr.create_device_manager is None:
        pytest.skip("python-roborock not installed")
    from services.homehub.tests.conftest import make_hub
    seen = {}

    async def fake_create(params, cache=None):
        seen["params"] = params
        return FakeManager(make_props())

    monkeypatch.setattr(rr, "create_device_manager", fake_create)
    hub, entry = make_hub("roborock", DATA, ENTITIES)
    driver = RoborockDriver(hub, entry)
    await driver.async_setup()
    p = seen["params"]
    assert p.username == "someone@example.com" and p.base_url == "https://usiot.roborock.com"
    assert p.user_data.rriot.k == "k" and p.user_data.token == "t"


async def test_unload_closes_manager():
    _hub, driver, _props, manager = await setup()
    await driver.async_unload()
    assert manager.closed
