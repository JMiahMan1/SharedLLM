"""HomeKit thermostats against a fake aiohomekit pairing (no network)."""
from __future__ import annotations

import copy

import pytest

from services.homehub.core import HubError, ServiceNotFound
from services.homehub.drivers import homekit
from services.homehub.drivers.homekit import HomeKitDriver, parse_unique_id, short_type
from services.homehub.tests.conftest import make_hub

PID = "82:5B:BB:0D:AF:0F"
HAP = "-0000-1000-8000-0026BB765291"


def ch(iid, t, value=None, perms=("pr", "ev"), **meta):
    d = {"iid": iid, "type": t if "-" in t else t.rjust(8, "0") + HAP, "perms": list(perms), "format": "float"}
    if value is not None:
        d["value"] = value
    d.update(meta)
    return d


# The ecobee's layout as HA cached it (aid 1, thermostat service iid 16).
ECOBEE = [{
    "aid": 1,
    "services": [
        {"iid": 1, "type": "0000003E" + HAP, "characteristics": [ch(6, "14", perms=("pw",))]},
        {"iid": 16, "type": "0000004A" + HAP, "characteristics": [
            ch(17, "F", 0, **{"valid-values": [0, 1, 2]}),
            ch(18, "33", 2, ("pr", "pw", "ev"), **{"valid-values": [0, 1, 2, 3], "minValue": 0, "maxValue": 3}),
            ch(19, "11", 23.5, unit="celsius", minValue=0, maxValue=40.0),
            ch(20, "35", 23.9, ("pr", "pw", "ev"), unit="celsius", minValue=7.2, maxValue=33.3),
            ch(21, "36", 1, ("pr", "pw", "ev"), **{"valid-values": [0, 1]}),
            ch(22, "D", 24.2, ("pr", "pw", "ev"), unit="celsius", minValue=18.3, maxValue=33.3),
            ch(23, "12", 21.4, ("pr", "pw", "ev"), unit="celsius", minValue=7.2, maxValue=26.1),
            ch(24, "10", 52.0, unit="percentage"),
            ch(25, "34", 36.0, ("pr", "pw", "ev"), minValue=20.0, maxValue=50.0),
            ch(75, "BF", 1, ("pr", "pw", "ev")),
            ch(76, "AF", 0),
            ch(33, "B7DDB9A3-54BB-4572-91D2-F1F5B0510F8C", 1, ("pr",)),
            ch(40, "1B300BC2-CBE4-4BDE-8D4A-0F7C2C4D4C8F", None, ("pw",)),
            ch(48, "FA128DE6-9D7D-49A4-B6D8-4E4E234DEE38", None, ("pw",)),
        ]},
    ],
}]

# The Resideo T5: heat/cool/off only, no range, no humidity, no fan.
T5 = [{
    "aid": 1,
    "services": [
        {"iid": 1, "type": "0000003E" + HAP, "characteristics": [ch(2, "14", perms=("pw",))]},
        {"iid": 272, "type": "0000004A" + HAP, "characteristics": [
            ch(275, "F", 2, minValue=0, maxValue=2),
            ch(276, "33", 2, ("pr", "pw", "ev"), **{"valid-values": [0, 1, 2]}),
            ch(277, "11", 24, unit="celsius"),
            ch(278, "35", 24, ("pr", "pw", "ev"), unit="celsius", minValue=10, maxValue=32),
            ch(279, "36", 1, ("pr", "pw", "ev")),
        ]},
    ],
}]


class FakePairing:
    def __init__(self, accessories):
        self.accessories = copy.deepcopy(accessories)
        self.values = {(a["aid"], c["iid"]): c.get("value")
                       for a in self.accessories for s in a["services"] for c in s["characteristics"]}
        self.listeners = []
        self.subscribed = None
        self.puts = []
        self.fail_reads = False
        self.closed = False

    async def list_accessories_and_characteristics(self):
        return copy.deepcopy(self.accessories)

    def dispatcher_connect(self, cb):
        self.listeners.append(cb)
        return lambda: self.listeners.remove(cb)

    def dispatcher_availability_changed(self, cb):
        return lambda: None

    async def subscribe(self, chars):
        self.subscribed = set(chars)

    async def get_characteristics(self, chars):
        if self.fail_reads:
            raise OSError("no route to host")
        return {k: {"value": self.values.get(k)} for k in chars}

    async def put_characteristics(self, writes):
        self.puts.append(list(writes))
        for aid, iid, v in writes:
            self.values[(aid, iid)] = v
        return {}

    async def close(self):
        self.closed = True

    def push(self, event):
        for cb in list(self.listeners):
            cb(event)


ECOBEE_ENTITIES = [
    {"entity_id": "climate.downstairs", "unique_id": f"{PID}_1_16", "supported_features": 399},
    {"entity_id": "sensor.downstairs_current_temperature", "unique_id": f"{PID}_1_16_19",
     "unit_of_measurement": "°F", "original_device_class": "temperature",
     "capabilities": {"state_class": "measurement"}},
    {"entity_id": "sensor.downstairs_current_humidity", "unique_id": f"{PID}_1_16_24",
     "unit_of_measurement": "%", "original_device_class": "humidity"},
    {"entity_id": "select.downstairs_temperature_display_units", "unique_id": f"{PID}_1_16_21",
     "capabilities": {"options": ["celsius", "fahrenheit"]}},
    {"entity_id": "select.downstairs_current_mode", "unique_id": f"{PID}_1_16_33",
     "capabilities": {"options": ["home", "sleep", "away"]}},
    {"entity_id": "button.downstairs_identify", "unique_id": f"{PID}_1_1_6"},
    {"entity_id": "button.downstairs_clear_hold", "unique_id": f"{PID}_1_16_48"},
]


async def setup(accessories=ECOBEE, entities=ECOBEE_ENTITIES, unit_system="us_customary"):
    hub, entry = make_hub("homekit_controller", {"Connection": "IP", "AccessoryPairingID": PID,
                                                 "AccessoryIP": "192.0.2.10", "AccessoryPort": 1},
                          entities)
    hub.location["unit_system"] = unit_system
    fake = FakePairing(accessories)
    driver = HomeKitDriver(hub, entry)

    async def make_pairing():
        return fake

    driver._make_pairing = make_pairing
    await driver.async_setup()
    hub.drivers[entry.entry_id] = driver
    return hub, driver, fake


def test_unique_id_and_type_parsing():
    assert parse_unique_id(f"{PID}_1_16") == (PID, 1, 16, None)
    assert parse_unique_id(f"{PID}_1_16_19") == (PID, 1, 16, 19)
    assert parse_unique_id("garbage") is None
    assert short_type("0000004A" + HAP) == "4A"
    assert short_type("4A") == "4A"
    assert short_type("B7DDB9A3-54BB-4572-91D2-F1F5B0510F8C") == "B7DDB9A3"


async def test_ecobee_climate_state_matches_ha():
    hub, driver, fake = await setup()
    st = hub.states.get("climate.downstairs")
    assert st.state == "cool"
    a = st.attributes
    assert a["hvac_modes"] == ["off", "heat", "cool", "heat_cool"]
    assert (a["min_temp"], a["max_temp"]) == (45, 92)  # what HA's registry recorded
    assert a["current_temperature"] == 74 and a["temperature"] == 75
    assert a["target_temp_high"] is None and a["target_temp_low"] is None
    assert a["current_humidity"] == 52.0 and a["humidity"] == 36.0
    assert (a["min_humidity"], a["max_humidity"]) == (20, 50)
    assert a["fan_mode"] == "auto" and a["fan_modes"] == ["on", "auto"]
    assert a["hvac_action"] == "idle"
    assert a["temperature_unit"] == "°F"
    assert a["supported_features"] == 399
    assert driver.climate_features((1, 16)) == 399
    # every event-capable characteristic is subscribed
    assert (1, 19) in fake.subscribed and (1, 33) not in fake.subscribed


async def test_ecobee_sensors_and_selects():
    hub, _driver, _fake = await setup()
    t = hub.states.get("sensor.downstairs_current_temperature")
    assert t.state == "74.3" and t.attributes["unit_of_measurement"] == "°F"
    assert t.attributes["state_class"] == "measurement"
    assert hub.states.get("sensor.downstairs_current_humidity").state == "52.0"
    assert hub.states.get("select.downstairs_temperature_display_units").state == "fahrenheit"
    assert hub.states.get("select.downstairs_current_mode").state == "sleep"
    assert hub.states.get("button.downstairs_identify").state == "unknown"


async def test_heat_cool_shows_the_range():
    hub, _driver, fake = await setup()
    fake.push({(1, 18): {"value": 3}})
    a = hub.states.get("climate.downstairs").attributes
    assert hub.states.get("climate.downstairs").state == "heat_cool"
    assert a["temperature"] is None
    assert (a["target_temp_low"], a["target_temp_high"]) == (71, 76)


async def test_events_update_state_and_fan_action():
    hub, _driver, fake = await setup()
    fake.push({(1, 17): {"value": 2}})
    assert hub.states.get("climate.downstairs").attributes["hvac_action"] == "cooling"
    fake.push({(1, 17): {"value": 0}, (1, 76): {"value": 2}})
    assert hub.states.get("climate.downstairs").attributes["hvac_action"] == "fan"
    fake.push({(1, 18): {"value": 0}, (1, 17): {"value": 1}})
    st = hub.states.get("climate.downstairs")
    assert st.state == "off" and st.attributes["hvac_action"] == "idle"


async def test_set_temperature_converts_to_celsius():
    hub, _driver, fake = await setup()
    await hub.call_service("climate", "set_temperature", {"entity_id": "climate.downstairs", "temperature": 72})
    assert fake.puts[-1] == [(1, 20, 22.2)]
    assert hub.states.get("climate.downstairs").attributes["temperature"] == 72


async def test_set_temperature_range_with_mode():
    hub, _driver, fake = await setup()
    await hub.call_service("climate", "set_temperature", {
        "entity_id": "climate.downstairs", "hvac_mode": "heat_cool",
        "target_temp_low": 68, "target_temp_high": 78})
    writes = {(a, i): v for a, i, v in fake.puts[-1]}
    assert writes[(1, 18)] == 3
    assert writes[(1, 23)] == 20.0 and writes[(1, 22)] == 25.6
    assert writes[(1, 20)] == pytest.approx(22.8, abs=0.06)
    a = hub.states.get("climate.downstairs").attributes
    assert (a["target_temp_low"], a["target_temp_high"]) == (68, 78)


async def test_set_temperature_clamps_to_device_limits():
    hub, _driver, fake = await setup()
    await hub.call_service("climate", "set_temperature", {"entity_id": "climate.downstairs", "temperature": 120})
    assert fake.puts[-1] == [(1, 20, 33.3)]


async def test_modes_fan_and_on_off():
    hub, _driver, fake = await setup()
    await hub.call_service("climate", "set_hvac_mode", {"entity_id": "climate.downstairs", "hvac_mode": "heat"})
    assert fake.puts[-1] == [(1, 18, 1)]
    await hub.call_service("climate", "turn_off", {"entity_id": "climate.downstairs"})
    assert fake.puts[-1] == [(1, 18, 0)]
    assert hub.states.get("climate.downstairs").state == "off"
    await hub.call_service("climate", "turn_on", {"entity_id": "climate.downstairs"})
    assert fake.puts[-1] == [(1, 18, 1)]  # back to heat, not HA's first-listed heat_cool
    await hub.call_service("climate", "set_fan_mode", {"entity_id": "climate.downstairs", "fan_mode": "on"})
    assert fake.puts[-1] == [(1, 75, 0)]
    assert hub.states.get("climate.downstairs").attributes["fan_mode"] == "on"
    with pytest.raises(HubError):
        await hub.call_service("climate", "set_hvac_mode", {"entity_id": "climate.downstairs", "hvac_mode": "dry"})


async def test_selects_and_buttons_write_the_right_characteristic():
    hub, _driver, fake = await setup()
    await hub.call_service("select", "select_option",
                           {"entity_id": "select.downstairs_current_mode", "option": "away"})
    assert fake.puts[-1] == [(1, 40, 2)]
    await hub.call_service("select", "select_option",
                           {"entity_id": "select.downstairs_temperature_display_units", "option": "celsius"})
    assert fake.puts[-1] == [(1, 21, 0)]
    await hub.call_service("button", "press", {"entity_id": "button.downstairs_clear_hold"})
    assert fake.puts[-1] == [(1, 48, True)]
    assert hub.states.get("button.downstairs_clear_hold").state not in ("unknown", "unavailable")
    await hub.call_service("button", "press", {"entity_id": "button.downstairs_identify"})
    assert fake.puts[-1] == [(1, 6, True)]


async def test_t5_has_no_range_and_reports_its_target():
    entities = [{"entity_id": "climate.tstat_3692f3_t5_thermostat", "unique_id": "43:96:C5:87:43:96_1_272",
                 "supported_features": 385},
                {"entity_id": "sensor.tstat_3692f3_current_temperature",
                 "unique_id": "43:96:C5:87:43:96_1_272_277", "unit_of_measurement": "°F"}]
    hub, driver, _fake = await setup(T5, entities)
    st = hub.states.get("climate.tstat_3692f3_t5_thermostat")
    assert st.state == "cool"
    a = st.attributes
    assert a["hvac_modes"] == ["off", "heat", "cool"]
    assert (a["min_temp"], a["max_temp"]) == (50, 90)
    assert a["temperature"] == 75 and a["current_temperature"] == 75
    assert "target_temp_high" not in a and "current_humidity" not in a and "fan_mode" not in a
    assert driver.climate_features((1, 272)) == 385
    assert a["hvac_action"] == "cooling"
    with pytest.raises(ServiceNotFound):
        await hub.call_service("climate", "set_fan_mode",
                               {"entity_id": "climate.tstat_3692f3_t5_thermostat", "fan_mode": "on"})


async def test_metric_household_gets_celsius():
    hub, _driver, fake = await setup(unit_system="metric")
    a = hub.states.get("climate.downstairs").attributes
    assert a["temperature"] == 23.9 and a["temperature_unit"] == "°C"
    await hub.call_service("climate", "set_temperature", {"entity_id": "climate.downstairs", "temperature": 22.5})
    assert fake.puts[-1] == [(1, 20, 22.5)]


async def test_poll_failure_marks_unavailable_then_recovers():
    hub, driver, fake = await setup()
    fake.fail_reads = True
    await driver.async_poll()
    assert hub.states.get("climate.downstairs").state == "unavailable"
    assert hub.states.get("sensor.downstairs_current_temperature").state == "unavailable"
    fake.fail_reads = False
    fake.values[(1, 19)] = 25.0
    await driver.async_poll()
    assert hub.states.get("climate.downstairs").state == "cool"
    assert hub.states.get("sensor.downstairs_current_temperature").state == "77.0"


async def test_setup_raises_when_the_accessory_is_silent():
    hub, entry = make_hub("homekit_controller", {"Connection": "IP"}, ECOBEE_ENTITIES)
    driver = HomeKitDriver(hub, entry)

    class Silent(FakePairing):
        async def list_accessories_and_characteristics(self):
            raise OSError("timed out")

    async def make_pairing():
        return Silent(ECOBEE)

    driver._make_pairing = make_pairing
    with pytest.raises(homekit.DeviceUnavailable):
        await driver.async_setup()


async def test_non_ip_pairing_is_refused():
    hub, entry = make_hub("homekit_controller", {"Connection": "BLE"}, ECOBEE_ENTITIES)
    with pytest.raises(HubError):
        await HomeKitDriver(hub, entry).async_setup()


async def test_unload_closes_the_session():
    _hub, driver, fake = await setup()
    await driver.async_unload()
    assert fake.closed and not fake.listeners


async def test_set_temperature_rounds_to_the_accessory_step():
    """The T5 steps by 0.5 C and refused 75 F sent as 23.89 C (HAP -70410)."""
    hub, driver, fake = await setup()
    driver.chars[(1, 20)]["step"] = 0.5
    await hub.call_service("climate", "set_temperature", {"entity_id": "climate.downstairs", "temperature": 75})
    assert fake.puts[-1] == [(1, 20, 24.0)]
    driver.chars[(1, 20)]["step"] = 0.1
    await hub.call_service("climate", "set_temperature", {"entity_id": "climate.downstairs", "temperature": 75})
    assert fake.puts[-1] == [(1, 20, pytest.approx(23.9))]
