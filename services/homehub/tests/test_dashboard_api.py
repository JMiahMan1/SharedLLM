"""The Rooms and Climate pages' data, built from the registry alone."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import dashboard_api
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import Area, DeviceEntry, EntityEntry, Hub
from services.homehub.runtime import Runtime
from services.homehub.store import Store

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}


def add(hub, eid, state, device=None, area=None, dc=None, **attrs):
    hub.entities[eid] = EntityEntry(entity_id=eid, platform="test", device_id=device, area_id=area,
                                    original_device_class=dc)
    if dc:
        attrs.setdefault("device_class", dc)
    hub.states.set(eid, state, attrs)


@pytest.fixture
def hub():
    hub = Hub()
    hub.areas["hall"] = Area("hall", "Hall", floor_id="ground")
    hub.areas["attic"] = Area("attic", "Attic")
    hub.floors = {"ground": {"floor_id": "ground", "name": "Ground", "level": 0}}
    hub.devices["tstat"] = DeviceEntry(id="tstat", name="Hall Thermostat", area_id="hall")
    hub.devices["ac"] = DeviceEntry(id="ac", name="Hall AC", area_id="hall")
    add(hub, "climate.hall", "cool", device="tstat", current_temperature=77, temperature=76)
    add(hub, "sensor.hall_ac_power", "1.2", device="ac", dc="power", unit_of_measurement="kW")
    add(hub, "sensor.hall_temp", "75", area="hall", dc="temperature", unit_of_measurement="°F")
    add(hub, "light.hall", "on", area="hall", brightness=128)
    add(hub, "camera.hall", "idle", area="hall", access_token="secret-token", entity_picture="/x?token=secret-token")
    add(hub, "sensor.hall_wifi", "-60", area="hall", dc="signal_strength")
    add(hub, "update.hall_fw", "off", area="hall")
    add(hub, "switch.loose", "off")
    add(hub, "climate.attic", "off", area="attic")
    add(hub, "sensor.attic_fan_power", "30", dc="power", unit_of_measurement="W", friendly_name="Attic Fan Power")
    hub.entities["light.hall_hidden"] = EntityEntry(entity_id="light.hall_hidden", platform="test", area_id="hall",
                                                    hidden_by="user")
    hub.states.set("light.hall_hidden", "off", {})
    return hub


def test_rooms_group_by_area_and_skip_setup_entities(hub):
    out = dashboard_api.rooms(hub, {"hidden": []})
    hall = next(a for a in out["areas"] if a["area_id"] == "hall")
    ids = [e["entity_id"] for e in hall["entities"]]
    assert ids == ["camera.hall", "climate.hall", "light.hall", "sensor.hall_ac_power", "sensor.hall_temp"]
    assert [e["entity_id"] for e in out["unassigned"]] == ["sensor.attic_fan_power", "switch.loose"]
    cam = next(e for e in hall["entities"] if e["domain"] == "camera")
    assert "access_token" not in cam["attributes"] and "entity_picture" not in cam["attributes"]
    assert out["floors"] == [{"floor_id": "ground", "name": "Ground", "level": 0}]


def test_hidden_entities_stay_off_the_room_cards(hub):
    out = dashboard_api.rooms(hub, {"hidden": ["light.hall"]})
    hall = next(a for a in out["areas"] if a["area_id"] == "hall")
    assert "light.hall" not in [e["entity_id"] for e in hall["entities"]]


def test_thermostat_power_comes_from_its_area_then_its_name_then_settings(hub):
    out = {t["entity_id"]: t for t in dashboard_api.climate(hub, {"climate_power": {}})["thermostats"]}
    assert [p["entity_id"] for p in out["climate.hall"]["power"]] == ["sensor.hall_ac_power"]
    assert [s["entity_id"] for s in out["climate.hall"]["sensors"]] == ["sensor.hall_temp"]
    # no power sensor in the attic: matched by the area's name instead
    assert [p["entity_id"] for p in out["climate.attic"]["power"]] == ["sensor.attic_fan_power"]
    override = dashboard_api.climate(hub, {"climate_power": {"climate.hall": []}})["thermostats"]
    assert next(t for t in override if t["entity_id"] == "climate.hall")["power"] == []


def test_routes_and_admin_prefs(hub, tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub.store = store
    client = TestClient(build_app(Runtime(hub, store), Auth(store, SECRET)))
    assert client.get("/api/homehub/dashboard/rooms").status_code == 401
    assert client.get("/api/homehub/dashboard/rooms", headers=H).json()["areas"][0]["name"] == "Attic"
    r = client.put("/hub/dashboard/prefs", headers=H, json={"climate_power": {"climate.hall": ["sensor.hall_ac_power"]},
                                                            "hidden": ["switch.loose"]})
    assert r.status_code == 200
    assert client.get("/api/homehub/dashboard/prefs", headers=H).json()["hidden"] == ["switch.loose"]
    t = client.get("/api/homehub/dashboard/climate", headers=H).json()["thermostats"]
    assert next(x for x in t if x["entity_id"] == "climate.hall")["power_source"] == "settings"
    assert client.put("/hub/dashboard/prefs", headers=H, json={"hidden": "x"}).status_code == 400
    store.close()


def test_an_unavailable_light_says_whether_its_device_or_its_integration_is_out():
    from services.homehub.core import ConfigEntry
    hub = Hub()
    hub.config_entries["m"] = ConfigEntry("m", "matter")
    hub.config_entries["m"].state = "loaded"
    hub.config_entries["w"] = ConfigEntry("w", "wiz")
    hub.config_entries["w"].state = "setup_retry"
    for dev in ("bulb1", "bulb2"):
        hub.devices[dev] = DeviceEntry(id=dev, name=dev)
    for eid, dev, cfg, state in (("light.bulb1", "bulb1", "m", "unavailable"), ("light.bulb2", "bulb2", "m", "on"),
                                 ("light.porch", None, "w", "unavailable")):
        hub.entities[eid] = EntityEntry(entity_id=eid, platform="test", device_id=dev, config_entry_id=cfg)
        hub.states.set(eid, state, {})
    # One bulb of several stopped answering: that bulb has no power.
    assert dashboard_api.offline_cause(hub, "light.bulb1") == {"cause": "device", "integration": "Matter"}
    assert dashboard_api.offline_cause(hub, "light.bulb2") is None
    # Every device of the integration at once: the integration (a Matter server down), not each bulb.
    hub.states.set("light.bulb2", "unavailable", {})
    assert dashboard_api.offline_cause(hub, "light.bulb1")["cause"] == "integration"
    # An integration that is not running.
    assert dashboard_api.offline_cause(hub, "light.porch") == {"cause": "integration", "integration": "WiZ"}
    assert dashboard_api.summarize(hub, "light.porch")["offline"]["cause"] == "integration"
