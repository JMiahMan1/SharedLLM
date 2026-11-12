"""switch_as_x: mirrors the wrapped switch and forwards commands to switch.* services."""
from __future__ import annotations

import pytest

from services.homehub.core import ConfigEntry, Driver, EntityEntry, ServiceNotFound
from services.homehub.drivers.switch_as_x import SwitchAsXDriver
from services.homehub.tests.conftest import make_hub

SOURCE = "switch.string_lights_front"
WRAPPER = "light.kauf_plug_d67371_kauf_plug"


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
    hub, entry = make_hub("switch_as_x", {}, [{"entity_id": wrapper, "unique_id": "01KAYEKT4BZJCYGA2NFJMCVBSV"}],
                          options={"entity_id": SOURCE, "invert": invert, "target_domain": target_domain})
    # The wrapped switch belongs to another entry, as it does in the real registry.
    other = ConfigEntry(entry_id="esphome1", domain="esphome")
    hub.config_entries[other.entry_id] = other
    hub.entities[SOURCE] = EntityEntry(entity_id=SOURCE, platform="esphome", config_entry_id="esphome1",
                                       unique_id="C4:4F:33:D6:73:71/0/switch/Kauf Plug")
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
