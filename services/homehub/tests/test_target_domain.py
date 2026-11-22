"""A room or a device target runs a service on that service's own entities only (HA's rule).

Seen in production: ``light.turn_on`` on a bulb's device also tried its signal-strength,
reboot-count and uptime sensors, which logged "partly failed: unknown entity" for each.
"""
from __future__ import annotations

import pytest

from services.homehub.core import Area, DeviceEntry, EntityEntry, Hub


@pytest.fixture
def hub() -> Hub:
    hub = Hub(time_zone="Etc/GMT+7")
    hub.areas["bedroom"] = Area("bedroom", "Bedroom")
    hub.devices["lamp"] = DeviceEntry(id="lamp", name="Bed Lamp", area_id="bedroom")
    hub.entities["light.bed_lamp"] = EntityEntry("light.bed_lamp", "esphome", "u1", device_id="lamp")
    hub.entities["sensor.bed_lamp_rssi"] = EntityEntry("sensor.bed_lamp_rssi", "esphome", "u2", device_id="lamp")
    hub.entities["sensor.bed_lamp_uptime"] = EntityEntry("sensor.bed_lamp_uptime", "esphome", "u3", device_id="lamp")
    hub.entities["switch.fan"] = EntityEntry("switch.fan", "tasmota", "u4", area_id="bedroom")
    for eid in hub.entities:
        hub.states.set(eid, "on")
    return hub


def test_a_device_target_keeps_only_the_services_domain(hub: Hub) -> None:
    assert hub.resolve_targets({"device_id": "lamp"}, "light") == ["light.bed_lamp"]


def test_an_area_target_keeps_only_the_services_domain(hub: Hub) -> None:
    assert hub.resolve_targets({"target": {"area_id": "bedroom"}}, "light") == ["light.bed_lamp"]
    assert hub.resolve_targets({"area_id": "bedroom"}, "switch") == ["switch.fan"]


def test_all_means_every_entity_of_the_domain(hub: Hub) -> None:
    assert hub.resolve_targets({"entity_id": "all"}, "switch") == ["switch.fan"]


def test_named_entities_are_kept_as_given(hub: Hub) -> None:
    assert hub.resolve_targets({"entity_id": ["sensor.bed_lamp_rssi"]}, "light") == ["sensor.bed_lamp_rssi"]


def test_homeassistant_services_take_every_domain(hub: Hub) -> None:
    got = hub.resolve_targets({"area_id": "bedroom"}, "homeassistant")
    assert set(got) == {"light.bed_lamp", "sensor.bed_lamp_rssi", "sensor.bed_lamp_uptime", "switch.fan"}


def test_without_a_domain_nothing_is_filtered(hub: Hub) -> None:
    assert len(hub.resolve_targets({"device_id": "lamp"})) == 3
