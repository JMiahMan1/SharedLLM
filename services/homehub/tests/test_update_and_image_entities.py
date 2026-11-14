"""Update entities (installed vs latest, skip and clear_skipped kept by the hub, install only where it can be done)
and image entities served through ``/api/image_proxy`` with the state's access token."""
from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.homehub import capabilities
from services.homehub.api import build_app
from services.homehub.auth import Auth
from services.homehub.core import ConfigEntry, DeviceEntry, DeviceUnavailable, Driver, EntityEntry, Hub, HubError
from services.homehub.runtime import Runtime
from services.homehub.store import Store

SECRET = "internal-secret"
H = {"Authorization": f"Bearer {SECRET}"}


class FakeFirmware(Driver):
    """A device that reports a firmware version, and a picture, the way the real drivers do."""

    platform = "fake"
    poll_interval = None

    def __init__(self, hub, entry):
        super().__init__(hub, entry)
        self.installs: list[str] = []
        self.features = 1
        self.latest = "2.0.0"

    def publish_versions(self):
        self.publish("update.porch_node_firmware", "off", {
            "installed_version": "1.0.0", "latest_version": self.latest, "in_progress": False,
            "release_url": "https://example.com/notes", "supported_features": self.features,
        })

    async def async_setup(self):
        self.publish_versions()
        self.publish("update.lamp_firmware", "off", {"installed_version": "3.1", "latest_version": "3.1",
                                                    "supported_features": 0})
        self.publish("image.robot_map", "unknown", {"access_token": "tok-1",
                                                    "entity_picture": "/api/image_proxy/image.robot_map?token=tok-1"})

    def access_tokens(self, entity_id):
        return ["tok-1"] if entity_id == "image.robot_map" else []

    async def async_image(self, entity):
        if self.hub.states.get(entity.entity_id).attributes.get("broken"):
            raise DeviceUnavailable("the robot did not send its map")
        return b"\x89PNG-map", "image/png"

    async def async_call(self, entity, domain, service, data):
        self.installs.append(entity.entity_id)


@pytest.fixture
def setup(tmp_path):
    store = Store(str(tmp_path / "hub.db"), Fernet.generate_key().decode())
    hub = Hub(time_zone="UTC")
    hub.store = store
    entry = ConfigEntry("e1", "fake", "Fake devices")
    hub.config_entries["e1"] = entry
    hub.devices["d1"] = DeviceEntry(id="d1", name="Porch Node", config_entries=["e1"])
    for eid in ("update.porch_node_firmware", "update.lamp_firmware", "image.robot_map"):
        hub.entities[eid] = EntityEntry(entity_id=eid, platform="fake", unique_id=eid, config_entry_id="e1",
                                        device_id="d1")
        store.save_entity(hub.entities[eid])
    driver = FakeFirmware(hub, entry)
    hub.drivers["e1"] = driver
    asyncio.run(driver.async_setup())
    return hub, store, driver


def test_an_offered_version_turns_the_update_on(setup):
    hub, _store, _driver = setup
    st = hub.states.get("update.porch_node_firmware")
    assert st.state == "on" and st.attributes["skipped_version"] is None
    assert hub.states.get("update.lamp_firmware").state == "off"


async def test_skip_is_kept_by_the_hub_and_survives_a_restart(setup):
    hub, store, driver = setup
    await hub.call_service("update", "skip", {"entity_id": "update.porch_node_firmware"})
    st = hub.states.get("update.porch_node_firmware")
    assert st.state == "off" and st.attributes["skipped_version"] == "2.0.0"
    # The driver publishing again does not bring the skipped version back.
    driver.publish_versions()
    assert hub.states.get("update.porch_node_firmware").state == "off"
    saved = {e.entity_id: e for e in store.load_entities()}["update.porch_node_firmware"]
    assert saved.options["update"]["skipped_version"] == "2.0.0"
    # A newer version than the skipped one is offered again.
    driver.latest = "2.1.0"
    driver.publish_versions()
    assert hub.states.get("update.porch_node_firmware").state == "on"
    driver.latest = "2.0.0"
    driver.publish_versions()
    await hub.call_service("update", "clear_skipped", {"entity_id": "update.porch_node_firmware"})
    st = hub.states.get("update.porch_node_firmware")
    assert st.state == "on" and st.attributes["skipped_version"] is None
    saved = {e.entity_id: e for e in store.load_entities()}["update.porch_node_firmware"]
    assert "update" not in saved.options


async def test_skip_with_nothing_to_skip_changes_nothing(setup):
    hub, _store, _driver = setup
    with pytest.raises(HubError, match="no update to skip"):
        await hub.call_service("update", "skip", {"entity_id": ["update.porch_node_firmware",
                                                                "update.lamp_firmware"]})
    assert hub.entities["update.porch_node_firmware"].options == {}  # all or nothing


async def test_install_only_where_the_integration_can(setup):
    hub, _store, driver = setup
    await hub.call_service("update", "install", {"entity_id": "update.porch_node_firmware"})
    assert driver.installs == ["update.porch_node_firmware"]
    with pytest.raises(HubError, match="cannot be installed"):
        await hub.call_service("update", "install", {"entity_id": "update.lamp_firmware"})
    assert driver.installs == ["update.porch_node_firmware"]


def test_capabilities_offer_install_only_with_the_feature(setup):
    hub, _store, _driver = setup
    cap = capabilities.entity_capabilities(hub, "update.porch_node_firmware")
    assert {"firmware", "update"} <= set(cap["abilities"])
    assert {"update.install", "update.skip", "update.clear_skipped"} <= set(cap["services"])
    assert cap["details"]["latest_version"] == "2.0.0" and cap["details"]["installed_version"] == "1.0.0"
    lamp = capabilities.entity_capabilities(hub, "update.lamp_firmware")
    assert "update" not in lamp["abilities"] and "update.install" not in lamp["services"]
    assert "update.skip" in lamp["services"]
    assert "image" in capabilities.entity_capabilities(hub, "image.robot_map")["abilities"]


def test_update_services_are_listed(setup):
    hub, _store, _driver = setup
    services = {d["domain"]: set(d["services"]) for d in hub.list_services()}
    assert {"install", "skip", "clear_skipped"} <= services["update"]


@pytest.fixture
def client(setup, tmp_path):
    hub, store, _driver = setup
    app = build_app(Runtime(hub, store), Auth(store, SECRET))
    with TestClient(app) as c:
        yield c


def test_image_proxy_takes_the_access_token_or_a_login(client):
    r = client.get("/api/image_proxy/image.robot_map?token=tok-1")
    assert r.status_code == 200 and r.content == b"\x89PNG-map" and r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "no-store"
    assert client.get("/api/image_proxy/image.robot_map", headers=H).status_code == 200
    assert client.get("/api/image_proxy/image.robot_map?token=wrong").status_code == 401
    assert client.get("/api/image_proxy/image.robot_map").status_code == 401


def test_image_proxy_errors_say_why(client, setup):
    hub, _store, _driver = setup
    assert client.get("/api/image_proxy/update.lamp_firmware", headers=H).status_code == 404
    assert client.get("/api/image_proxy/image.nothing", headers=H).status_code == 404
    st = hub.states.get("image.robot_map")
    hub.states.set("image.robot_map", st.state, {**st.attributes, "broken": True})
    r = client.get("/api/image_proxy/image.robot_map", headers=H)
    assert r.status_code == 503 and "did not send its map" in r.text
