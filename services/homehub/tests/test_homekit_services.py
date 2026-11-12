"""The generic HomeKit mapping: specs from any accessory layout, states and writes by service type.

A made-up bridge with one accessory per service kind, against the same fake
pairing the thermostat tests use. Every id here is made up.
"""
from __future__ import annotations

import pytest

from services.homehub.core import ServiceNotFound
from services.homehub.drivers import homekit_services as hks
from services.homehub.drivers.homekit import HomeKitDriver
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.test_drivers_homekit import ECOBEE, HAP, FakePairing, ch

PID = "1A:2B:3C:4D:5E:6F"
RW = ("pr", "pw", "ev")


def info(iid, name, model="Model X", serial="SN-0001"):
    return {"iid": iid, "type": "0000003E" + HAP, "characteristics": [
        ch(iid + 1, "14", perms=("pw",)), ch(iid + 2, "23", name, ("pr",)), ch(iid + 3, "20", "Example Co", ("pr",)),
        ch(iid + 4, "21", model, ("pr",)), ch(iid + 5, "30", serial, ("pr",)), ch(iid + 6, "52", "1.0.0", ("pr",))]}


def svc(iid, t, *chars):
    return {"iid": iid, "type": t.rjust(8, "0") + HAP, "characteristics": list(chars)}


BRIDGE = [
    {"aid": 1, "services": [info(1, "Bridge")]},
    {"aid": 2, "services": [info(1, "Porch Light"), svc(10, "43",
        ch(11, "25", True, RW, format="bool"), ch(12, "8", 50, RW, format="int", minValue=0, maxValue=100),
        ch(13, "13", 120.0, RW), ch(14, "2F", 80.0, RW),
        ch(15, "CE", 250, RW, format="uint32", minValue=140, maxValue=500))]},
    {"aid": 3, "services": [info(1, "Desk Outlet"), svc(10, "47", ch(11, "25", False, RW, format="bool"),
                                                         ch(12, "26", True, format="bool"))]},
    {"aid": 4, "services": [info(1, "Ceiling Fan"), svc(10, "B7",
        ch(11, "B0", 1, RW, format="uint8"), ch(12, "29", 50.0, RW, minValue=0, maxValue=100, minStep=25),
        ch(13, "28", 0, RW, format="int"), ch(14, "B6", 0, RW, format="uint8"))]},
    {"aid": 5, "services": [info(1, "Garage Door"), svc(10, "41",
        ch(11, "E", 1, format="uint8"), ch(12, "32", 1, RW, format="uint8"), ch(13, "24", False, format="bool"))]},
    {"aid": 6, "services": [info(1, "Bedroom Shade"), svc(10, "8C",
        ch(11, "6D", 40, format="uint8"), ch(12, "7C", 40, RW, format="uint8"), ch(13, "72", 2, format="uint8"),
        ch(14, "6F", None, ("pw",), format="bool"))]},
    {"aid": 7, "services": [info(1, "Front Lock"), svc(10, "45",
        ch(11, "1D", 1, format="uint8"), ch(12, "1E", 1, RW, format="uint8"))]},
    {"aid": 8, "services": [info(1, "Hall Sensor"),
                            svc(10, "8A", ch(11, "11", 20.0, unit="celsius")),
                            svc(20, "82", ch(21, "10", 45.0)),
                            svc(30, "84", ch(31, "6B", 120.5)),
                            svc(40, "85", ch(41, "22", True, format="bool")),
                            svc(50, "80", ch(51, "6A", 1, format="uint8")),
                            svc(60, "96", ch(61, "68", 87, format="uint8")),
                            svc(70, "83", ch(71, "70", 0, format="uint8")),
                            svc(80, "87", ch(81, "76", 0, format="uint8")),
                            svc(90, "7F", ch(91, "69", 0, format="uint8")),
                            svc(100, "86", ch(101, "71", 1, format="uint8"))]},
    {"aid": 9, "services": [info(1, "Alarm"), svc(10, "7E",
        ch(11, "66", 3, format="uint8"), ch(12, "67", 3, RW, format="uint8"))]},
    {"aid": 10, "services": [info(1, "Remote Button"), svc(10, "89",
        ch(11, "73", None, ("pr", "ev"), format="uint8", **{"valid-values": [0, 2]}))]},
]


def test_specs_follow_service_types_with_ha_unique_ids():
    specs = hks.entity_specs(PID, BRIDGE)
    by_uid = {s["unique_id"]: s for s in specs}
    assert by_uid[f"{PID}_2_10"]["domain"] == "light" and by_uid[f"{PID}_2_10"]["name"] is None
    assert by_uid[f"{PID}_3_10"]["domain"] == "switch" and by_uid[f"{PID}_3_10"]["original_device_class"] == "outlet"
    assert by_uid[f"{PID}_4_10"]["domain"] == "fan"
    assert by_uid[f"{PID}_5_10"]["original_device_class"] == "garage"
    assert by_uid[f"{PID}_6_10"]["domain"] == "cover"
    assert by_uid[f"{PID}_7_10"]["domain"] == "lock"
    assert by_uid[f"{PID}_9_10"]["domain"] == "alarm_control_panel"
    assert by_uid[f"{PID}_10_10"]["domain"] == "event"
    assert by_uid[f"{PID}_10_10"]["capabilities"]["event_types"] == ["single_press", "long_press"]
    sensors = {s["original_device_class"]: s for s in specs if s["aid"] == 8 and s["domain"] != "button"}
    assert set(sensors) == {"temperature", "humidity", "illuminance", "motion", "opening", "battery", "moisture",
                            "smoke", "carbon_monoxide", "occupancy"}
    # one main entity per accessory; the others get their own names
    assert sum(1 for s in specs if s["aid"] == 8 and s["name"] is None) == 1
    assert sensors["humidity"]["name"] == "Humidity"
    assert sensors["battery"]["entity_category"] == "diagnostic"
    # every accessory gets HA's Identify button on its information service
    assert by_uid[f"{PID}_2_1_2"] == {"aid": 2, "domain": "button", "name": "Identify",
                                      "entity_category": "diagnostic", "unique_id": f"{PID}_2_1_2"}


def test_a_thermostat_gets_the_characteristic_entities_ha_adds():
    specs = hks.entity_specs(PID, ECOBEE)
    uids = {s["unique_id"]: s for s in specs}
    assert uids[f"{PID}_1_16"]["domain"] == "climate"
    assert uids[f"{PID}_1_16_19"]["original_device_class"] == "temperature"
    assert uids[f"{PID}_1_16_24"]["unit_of_measurement"] == "%"
    assert uids[f"{PID}_1_16_21"]["domain"] == "select"


ENTS = [
    {"entity_id": "light.porch", "unique_id": f"{PID}_2_10"},
    {"entity_id": "switch.desk", "unique_id": f"{PID}_3_10"},
    {"entity_id": "fan.ceiling", "unique_id": f"{PID}_4_10"},
    {"entity_id": "cover.garage", "unique_id": f"{PID}_5_10"},
    {"entity_id": "cover.shade", "unique_id": f"{PID}_6_10"},
    {"entity_id": "lock.front", "unique_id": f"{PID}_7_10"},
    {"entity_id": "sensor.hall_temperature", "unique_id": f"{PID}_8_10"},
    {"entity_id": "sensor.hall_battery", "unique_id": f"{PID}_8_60"},
    {"entity_id": "binary_sensor.hall_motion", "unique_id": f"{PID}_8_40"},
    {"entity_id": "binary_sensor.hall_contact", "unique_id": f"{PID}_8_50"},
    {"entity_id": "binary_sensor.hall_leak", "unique_id": f"{PID}_8_70"},
    {"entity_id": "alarm_control_panel.alarm", "unique_id": f"{PID}_9_10"},
    {"entity_id": "event.remote", "unique_id": f"{PID}_10_10",
     "capabilities": {"event_types": ["single_press", "long_press"]}},
]


async def bridge():
    hub, entry = make_hub("homekit_controller", {"Connection": "IP", "AccessoryPairingID": PID,
                                                 "iOSPairingId": "made-up-controller"}, ENTS)
    fake = FakePairing(BRIDGE)
    driver = HomeKitDriver(hub, entry)

    async def make_pairing():
        return fake

    driver._make_pairing = make_pairing
    await driver.async_setup()
    hub.drivers[entry.entry_id] = driver
    return hub, driver, fake


async def test_states_by_type():
    hub, _drv, _fake = await bridge()
    light = hub.states.get("light.porch")
    assert light.state == "on"
    assert light.attributes["brightness"] == 128
    assert light.attributes["hs_color"] == (120.0, 80.0)
    assert light.attributes["color_temp_kelvin"] == 4000
    assert light.attributes["supported_color_modes"] == ["hs", "color_temp"]
    assert light.attributes["min_color_temp_kelvin"] == 2000
    assert hub.states.get("switch.desk").state == "off"
    assert hub.states.get("switch.desk").attributes["outlet_in_use"] is True
    fan = hub.states.get("fan.ceiling")
    assert fan.state == "on" and fan.attributes["percentage"] == 50 and fan.attributes["direction"] == "forward"
    assert fan.attributes["supported_features"] == 1 | 2 | 4 | 16 | 32
    assert hub.states.get("cover.garage").state == "closed"
    shade = hub.states.get("cover.shade")
    assert shade.state == "open" and shade.attributes["current_position"] == 40
    assert shade.attributes["supported_features"] == 15
    assert hub.states.get("lock.front").state == "locked"
    temp = hub.states.get("sensor.hall_temperature")
    assert temp.state == "68.0" and temp.attributes["unit_of_measurement"] == "°F"
    assert hub.states.get("sensor.hall_battery").state == "87"
    assert hub.states.get("binary_sensor.hall_motion").state == "on"
    assert hub.states.get("binary_sensor.hall_contact").state == "on"  # 1 = no contact = open
    assert hub.states.get("binary_sensor.hall_leak").state == "off"
    assert hub.states.get("alarm_control_panel.alarm").state == "disarmed"
    assert hub.states.get("event.remote").state == "unknown"


async def test_writes_by_type(monkeypatch):
    hub, _drv, fake = await bridge()
    await hub.call_service("light", "turn_on", {"entity_id": "light.porch", "brightness_pct": 30,
                                                "color_temp_kelvin": 2700})
    assert fake.puts[-1] == [(2, 11, True), (2, 12, 30), (2, 15, 370)]
    await hub.call_service("light", "turn_off", {"entity_id": "light.porch"})
    assert fake.puts[-1] == [(2, 11, False)]
    assert hub.states.get("light.porch").state == "off"
    await hub.call_service("switch", "toggle", {"entity_id": "switch.desk"})
    assert fake.puts[-1] == [(3, 11, True)]
    await hub.call_service("fan", "set_percentage", {"entity_id": "fan.ceiling", "percentage": 80})
    assert fake.puts[-1] == [(4, 11, 1), (4, 12, 75.0)]  # 80 % snapped to the 25 % step
    await hub.call_service("fan", "set_percentage", {"entity_id": "fan.ceiling", "percentage": 0})
    assert fake.puts[-1] == [(4, 11, 0)]
    await hub.call_service("fan", "set_direction", {"entity_id": "fan.ceiling", "direction": "reverse"})
    assert fake.puts[-1] == [(4, 13, 1)]
    await hub.call_service("cover", "open_cover", {"entity_id": "cover.garage"})
    assert fake.puts[-1] == [(5, 12, 0)]
    await hub.call_service("cover", "set_cover_position", {"entity_id": "cover.shade", "position": 70})
    assert fake.puts[-1] == [(6, 12, 70)]
    await hub.call_service("cover", "stop_cover", {"entity_id": "cover.shade"})
    assert fake.puts[-1] == [(6, 14, True)]
    await hub.call_service("lock", "unlock", {"entity_id": "lock.front"})
    assert fake.puts[-1] == [(7, 12, 0)]
    # the target moved, the bolt has not yet: HA shows "unlocking"
    assert hub.states.get("lock.front").state == "unlocking"
    await hub.call_service("alarm_control_panel", "alarm_arm_away", {"entity_id": "alarm_control_panel.alarm"})
    assert fake.puts[-1] == [(9, 12, 1)]
    with pytest.raises(ServiceNotFound):
        await hub.call_service("lock", "open", {"entity_id": "lock.front"})


async def test_a_button_press_arrives_as_an_event():
    hub, _drv, fake = await bridge()
    fake.push({(10, 11): {"value": 2}})
    st = hub.states.get("event.remote")
    assert st.state != "unknown" and st.attributes["event_type"] == "long_press"
    assert st.attributes["event_types"] == ["single_press", "long_press"]


async def test_unpair_removes_our_controller_pairing():
    _hub, drv, fake = await bridge()
    removed = []

    async def remove_pairing(pid):
        removed.append(pid)

    fake.remove_pairing = remove_pairing
    await drv.async_unpair()
    assert removed == ["made-up-controller"]
