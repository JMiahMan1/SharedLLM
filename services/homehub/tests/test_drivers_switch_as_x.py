"""switch_as_x: mirrors the wrapped switch and forwards commands to switch.* services."""
from __future__ import annotations

import pytest

from services.homehub.core import ConfigEntry, Driver, EntityEntry, ServiceNotFound
from services.homehub.drivers.switch_as_x import SwitchAsXDriver
from services.homehub.tests.conftest import make_hub

SOURCE = "switch.example_plug"
WRAPPER = "light.demo_plug_000001_demo_plug"


class FakeSwitchDriver(Driver):
    """Stands in for the ESPHome driver that owns the real switch."""

    platform = "esphome"

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.calls: list[str] = []

    async def async_call(self, entity, domain, service, data):
        self.calls.append(service)
        cur = self.hub.states.get(entity.entity_id)
        on = cur is not None and cur.state == "on"
        new = {"turn_on": True, "turn_off": False, "toggle": not on}[service]
        self.publish(entity, "on" if new else "off", {})


def setup_hub(invert: bool = False, target_domain: str = "light", wrapper: str = WRAPPER):
    hub, entry = make_hub("switch_as_x", {}, [{"entity_id": wrapper, "unique_id": "01J0000000000000000000SWAP"}],
                          options={"entity_id": SOURCE, "invert": invert, "target_domain": target_domain})
    # The wrapped switch belongs to another entry, as it does in the real registry.
    other = ConfigEntry(entry_id="esphome1", domain="esphome")
    hub.config_entries[other.entry_id] = other
    hub.entities[SOURCE] = EntityEntry(entity_id=SOURCE, platform="esphome", config_entry_id="esphome1",
                                       unique_id="02:00:00:00:01:0B/0/switch/Demo Plug")
    switch_driver = FakeSwitchDriver(hub, other)
    hub.drivers[other.entry_id] = switch_driver
    drv = SwitchAsXDriver(hub, entry)
    hub.drivers[entry.entry_id] = drv
    return hub, drv, switch_driver


async def test_mirrors_the_wrapped_switch_including_before_it_exists():
    hub, drv, _ = setup_hub()
    await drv.async_setup()
    assert hub.states.get(WRAPPER).state == "unavailable"
    hub.publish(SOURCE, "on", {})
    st = hub.states.get(WRAPPER)
    assert st.state == "on"
    assert st.attributes["supported_color_modes"] == ["onoff"] and st.attributes["color_mode"] == "onoff"
    hub.publish(SOURCE, "off", {})
    assert hub.states.get(WRAPPER).state == "off"
    assert hub.states.get(WRAPPER).attributes["color_mode"] is None
    hub.publish(SOURCE, "unavailable", {})
    assert hub.states.get(WRAPPER).state == "unavailable"


async def test_light_services_become_switch_services():
    hub, drv, sw = setup_hub()
    hub.publish(SOURCE, "off", {})
    await drv.async_setup()
    res = await hub.call_service("light", "turn_on", {"entity_id": WRAPPER})
    assert sw.calls == ["turn_on"]
    assert hub.states.get(WRAPPER).state == "on"
    assert any(s.entity_id == WRAPPER and s.state == "on" for s in res["changed_states"])
    await hub.call_service("light", "toggle", {"entity_id": WRAPPER})
    assert sw.calls[-1] == "toggle" and hub.states.get(WRAPPER).state == "off"
    await hub.call_service("light", "turn_off", {"entity_id": WRAPPER})
    assert sw.calls[-1] == "turn_off"


async def test_invert_swaps_state_and_commands():
    hub, drv, sw = setup_hub(invert=True)
    hub.publish(SOURCE, "on", {})
    await drv.async_setup()
    assert hub.states.get(WRAPPER).state == "off"
    await hub.call_service("light", "turn_on", {"entity_id": WRAPPER})
    assert sw.calls == ["turn_off"]
    assert hub.states.get(SOURCE).state == "off"
    assert hub.states.get(WRAPPER).state == "on"


async def test_other_target_domains():
    hub, drv, sw = setup_hub(target_domain="cover", wrapper="cover.gate")
    hub.publish(SOURCE, "off", {})
    await drv.async_setup()
    assert hub.states.get("cover.gate").state == "closed"
    await hub.call_service("cover", "open_cover", {"entity_id": "cover.gate"})
    assert sw.calls == ["turn_on"] and hub.states.get("cover.gate").state == "open"
    with pytest.raises(ServiceNotFound):
        await hub.call_service("cover", "set_cover_position", {"entity_id": "cover.gate", "position": 5})


async def test_unload_stops_following_the_switch():
    hub, drv, _ = setup_hub()
    hub.publish(SOURCE, "on", {})
    await drv.async_setup()
    await drv.async_unload()
    hub.publish(SOURCE, "off", {})
    assert hub.states.get(WRAPPER).state == "on"


# -- a door opener: a pulse relay and a contact sensor, shown as a garage door ------------------------------

SENSOR = "binary_sensor.demo_door_sensor"
GARAGE = "cover.demo_garage_door"


class PulseRelay(FakeSwitchDriver):
    """A relay that turns itself off after a press, like an ESPHome ``on_turn_on: delay, turn_off``."""

    def __init__(self, hub, entry, self_off: bool = True) -> None:
        super().__init__(hub, entry)
        self.self_off = self_off

    async def async_call(self, entity, domain, service, data):
        await super().async_call(entity, domain, service, data)
        if service == "turn_on" and self.self_off:
            self.publish(entity, "off", {})


def opener_hub(monkeypatch, *, momentary=True, sensor=True, invert=False, self_off=True):
    from services.homehub.drivers import switch_as_x

    monkeypatch.setattr(switch_as_x, "PULSE_S", 0)
    opts = {"entity_id": SOURCE, "invert": invert, "target_domain": "cover", "momentary": momentary,
            "device_class": "garage"}
    if sensor:
        opts["state_entity_id"] = SENSOR
    hub, entry = make_hub("switch_as_x", {}, [{"entity_id": GARAGE, "unique_id": "01J0000000000000000000GARG"}],
                          options=opts)
    other = ConfigEntry(entry_id="esphome1", domain="esphome")
    hub.config_entries[other.entry_id] = other
    hub.entities[SOURCE] = EntityEntry(entity_id=SOURCE, platform="esphome", config_entry_id="esphome1",
                                       unique_id="02:00:00:00:01:0B/0/switch/Opener")
    relay = PulseRelay(hub, other, self_off=self_off)
    hub.drivers[other.entry_id] = relay
    drv = SwitchAsXDriver(hub, entry)
    hub.drivers[entry.entry_id] = drv
    hub.publish(SOURCE, "off", {})
    return hub, drv, relay


async def test_a_garage_door_follows_its_sensor_and_says_what_it_is(monkeypatch):
    hub, drv, _ = opener_hub(monkeypatch)
    hub.publish(SENSOR, "off", {})
    await drv.async_setup()
    st = hub.states.get(GARAGE)
    assert st.state == "closed"
    assert st.attributes["device_class"] == "garage" and st.attributes["supported_features"] == 1 | 2 | 8
    hub.publish(SENSOR, "on", {})
    assert hub.states.get(GARAGE).state == "open"
    hub.publish(SENSOR, "unavailable", {})
    assert hub.states.get(GARAGE).state == "unavailable"


async def test_open_presses_only_when_closed_and_shows_opening_until_the_sensor_moves(monkeypatch):
    hub, drv, relay = opener_hub(monkeypatch)
    hub.publish(SENSOR, "off", {})
    await drv.async_setup()
    await hub.call_service("cover", "open_cover", {"entity_id": GARAGE})
    assert relay.calls == ["turn_on"]  # the relay let go by itself: no turn_off needed
    assert hub.states.get(GARAGE).state == "opening"
    await hub.call_service("cover", "open_cover", {"entity_id": GARAGE})
    assert relay.calls == ["turn_on"]  # already opening: no second press (that would stop it)
    hub.publish(SENSOR, "on", {})
    assert hub.states.get(GARAGE).state == "open"
    await hub.call_service("cover", "open_cover", {"entity_id": GARAGE})
    assert relay.calls == ["turn_on"]


async def test_close_presses_when_open_and_stop_presses_only_while_moving(monkeypatch):
    hub, drv, relay = opener_hub(monkeypatch, self_off=False)
    hub.publish(SENSOR, "on", {})
    await drv.async_setup()
    await hub.call_service("cover", "stop_cover", {"entity_id": GARAGE})
    assert relay.calls == []  # not moving: nothing to stop
    await hub.call_service("cover", "close_cover", {"entity_id": GARAGE})
    assert relay.calls == ["turn_on", "turn_off"]  # a relay that stays on is let go after the press
    assert hub.states.get(GARAGE).state == "closing"
    await hub.call_service("cover", "stop_cover", {"entity_id": GARAGE})
    assert relay.calls == ["turn_on", "turn_off", "turn_on", "turn_off"]
    assert hub.states.get(GARAGE).state == "open"


async def test_toggle_presses_and_the_moving_state_times_out(monkeypatch):
    hub, drv, relay = opener_hub(monkeypatch)
    drv.travel_s = 0.05
    hub.publish(SENSOR, "on", {})
    await drv.async_setup()
    await hub.call_service("cover", "toggle", {"entity_id": GARAGE})
    assert relay.calls == ["turn_on"] and hub.states.get(GARAGE).state == "closing"
    import asyncio

    await asyncio.sleep(0.1)  # the door never closed (blocked): back to what the sensor says
    assert hub.states.get(GARAGE).state == "open"


async def test_an_inverted_sensor_and_an_opener_without_a_sensor(monkeypatch):
    hub, drv, _ = opener_hub(monkeypatch, invert=True)
    hub.publish(SENSOR, "on", {})
    await drv.async_setup()
    assert hub.states.get(GARAGE).state == "closed"
    hub, drv, relay = opener_hub(monkeypatch, sensor=False)
    await drv.async_setup()
    st = hub.states.get(GARAGE)
    assert st.state == "unknown" and st.attributes["assumed_state"] is True
    await hub.call_service("cover", "open_cover", {"entity_id": GARAGE})
    await hub.call_service("cover", "open_cover", {"entity_id": GARAGE})
    assert relay.calls == ["turn_on", "turn_on"]  # it cannot know, so every request presses


async def test_a_sensor_cover_with_a_plain_switch_turns_it_on_and_off(monkeypatch):
    hub, drv, relay = opener_hub(monkeypatch, momentary=False)
    hub.publish(SENSOR, "off", {})
    await drv.async_setup()
    assert hub.states.get(GARAGE).attributes["supported_features"] == 1 | 2
    await hub.call_service("cover", "open_cover", {"entity_id": GARAGE})
    assert relay.calls == ["turn_on"]
    with pytest.raises(ServiceNotFound):
        await hub.call_service("cover", "stop_cover", {"entity_id": GARAGE})


async def test_a_plain_cover_is_still_a_switch_mirror(monkeypatch):
    hub, drv, sw = setup_hub(target_domain="cover", wrapper="cover.demo_plug")
    hub.publish(SOURCE, "on", {})
    await drv.async_setup()
    st = hub.states.get("cover.demo_plug")
    assert st.state == "open" and st.attributes["supported_features"] == 3 and "device_class" not in st.attributes
    await hub.call_service("cover", "close_cover", {"entity_id": "cover.demo_plug"})
    assert sw.calls == ["turn_off"]
