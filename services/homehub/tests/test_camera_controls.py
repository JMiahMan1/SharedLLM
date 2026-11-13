"""Feature discovery for the camera view: what each camera's device can do, grouped and ordered.

Made-up devices: a Reolink-style camera with a siren and settings, and an ESP32 garage board whose relay and
door sensor become one opener.
"""
from __future__ import annotations

from services.homehub import camera_controls
from services.homehub.core import DeviceEntry, EntityEntry
from services.homehub.tests.conftest import make_hub


def _add(hub, eid, device, state, *, category=None, device_class=None, hidden=False, disabled=False, **attrs):
    hub.entities[eid] = EntityEntry(eid, "test", eid, "entry1", device, entity_category=category,
                                    original_name=eid.split(".", 1)[1].replace("_", " ").title(),
                                    hidden_by="user" if hidden else None, disabled_by="user" if disabled else None)
    if device_class:
        attrs["device_class"] = device_class
    if not disabled:
        hub.publish(eid, state, attrs)


def _hub():
    hub, _entry = make_hub()
    for dev in ("cam", "door"):
        hub.devices[dev] = DeviceEntry(id=dev, name=dev, config_entries=["entry1"])
    # A Reolink-style camera.
    _add(hub, "camera.yard", "cam", "idle")
    _add(hub, "siren.yard_siren", "cam", "unknown")
    _add(hub, "binary_sensor.yard_person", "cam", "on")
    _add(hub, "binary_sensor.yard_motion", "cam", "off", device_class="motion")
    _add(hub, "sensor.yard_battery", "cam", "64", category="diagnostic", device_class="battery")
    _add(hub, "number.yard_volume", "cam", "90", category="config", min=0, max=100, step=1)
    _add(hub, "switch.yard_siren_on_event", "cam", "off", category="config")
    _add(hub, "light.yard_status_led", "cam", "off", category="config")
    _add(hub, "update.yard_firmware", "cam", "off", category="config")
    _add(hub, "switch.yard_hidden", "cam", "off", hidden=True)
    _add(hub, "switch.yard_disabled", "cam", "off", disabled=True)
    # An ESP32 garage board: camera, relay and door sensor.
    _add(hub, "camera.garage", "door", "idle")
    _add(hub, "switch.garage_door", "door", "off")
    _add(hub, "binary_sensor.garage_door_sensor", "door", "on", device_class="door")
    _add(hub, "sensor.garage_temperature", "door", "72", category="diagnostic", device_class="temperature")
    return hub


def test_a_reolink_style_camera_offers_its_siren_detections_and_settings():
    found = camera_controls.discover(_hub(), "camera.yard")
    assert [a["entity_id"] for a in found["actions"]] == ["siren.yard_siren"]
    assert found["actions"][0]["role"] == "siren"
    # Detections first (motion, then person), the battery last.
    assert [s["entity_id"] for s in found["status"]] == [
        "binary_sensor.yard_motion", "binary_sensor.yard_person", "sensor.yard_battery"]
    assert {s["entity_id"] for s in found["settings"]} == {
        "number.yard_volume", "switch.yard_siren_on_event", "light.yard_status_led"}
    every = {i["entity_id"] for g in ("actions", "status", "settings") for i in found[g]}
    assert not every & {"update.yard_firmware", "switch.yard_hidden", "switch.yard_disabled", "camera.yard"}
    volume = next(s for s in found["settings"] if s["entity_id"] == "number.yard_volume")
    assert volume["attributes"]["max"] == 100  # what a slider needs


def test_a_garage_boards_relay_and_door_sensor_become_one_opener():
    found = camera_controls.discover(_hub(), "camera.garage")
    [opener] = found["actions"]
    assert opener["role"] == "opener" and opener["entity_id"] == "switch.garage_door"
    assert opener["door"]["entity_id"] == "binary_sensor.garage_door_sensor" and opener["door"]["state"] == "on"
    assert "binary_sensor.garage_door_sensor" not in {s["entity_id"] for s in found["status"]}
    assert [s["entity_id"] for s in found["status"]] == ["sensor.garage_temperature"]


def test_two_switches_are_not_guessed_into_an_opener():
    hub = _hub()
    _add(hub, "switch.garage_light", "door", "off")
    found = camera_controls.discover(hub, "camera.garage")
    assert {a["role"] for a in found["actions"]} == {"switch"}
    assert "binary_sensor.garage_door_sensor" in {s["entity_id"] for s in found["status"]}


def test_only_cameras_are_discovered():
    hub = _hub()
    assert camera_controls.discover(hub, "switch.garage_door") is None
    assert camera_controls.discover(hub, "camera.nope") is None


def test_a_camera_with_no_device_has_nothing_else():
    hub = _hub()
    hub.entities["camera.loose"] = EntityEntry("camera.loose", "test", "loose", "entry1", None)
    hub.publish("camera.loose", "idle", {})
    assert camera_controls.discover(hub, "camera.loose") == {
        "entity_id": "camera.loose", "device_id": None, "actions": [], "status": [], "settings": []}
