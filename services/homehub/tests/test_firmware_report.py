"""The Firmware page's list: every updateable thing, what it runs, and what can be done about it."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.homehub import firmware
from services.homehub.core import UPDATE_INSTALL, DeviceUnavailable
from services.homehub.tests.conftest import make_hub


def _hub_with_firmware():
    hub, entry = make_hub(
        domain="esphome",
        entities=[
            {
                "entity_id": "update.office_node_firmware",
                "unique_id": "AA:BB:CC:DD:EE:FF",
                "name": "Firmware",
                "supported_features": UPDATE_INSTALL,
            }
        ],
    )
    hub.devices["dev1"].sw_version = "2026.10.17"
    hub.publish(
        "update.office_node_firmware",
        "off",
        {
            "installed_version": "2026.10.17",
            "latest_version": "2026.11.18",
            "supported_features": UPDATE_INSTALL,
            "in_progress": False,
        },
    )
    return hub, entry


def test_a_newer_version_is_listed_with_its_versions():
    hub, _ = _hub_with_firmware()
    row = firmware.report(hub)["devices"][0]
    assert row["entity_id"] == "update.office_node_firmware"
    assert row["device"] == "Test Device"
    assert row["integration"] == "esphome"
    assert row["installed_version"] == "2026.10.17"
    assert row["latest_version"] == "2026.11.18"
    assert row["available"] is True
    assert row["installable"] is True
    assert row["action"] == "install"


def test_an_older_version_is_not_offered():
    hub, _ = _hub_with_firmware()
    hub.publish(
        "update.office_node_firmware",
        "on",
        {
            "installed_version": "2026.11.18",
            "latest_version": "2026.10.17",
            "supported_features": UPDATE_INSTALL,
        },
    )
    row = firmware.report(hub)["devices"][0]
    assert row["available"] is False
    assert row["installed_version"] == "2026.11.18"


def test_an_update_that_cannot_be_installed_says_so():
    hub, _ = _hub_with_firmware()
    hub.publish(
        "update.office_node_firmware",
        "on",
        {
            "installed_version": "1.0",
            "latest_version": "2.0",
            "supported_features": 0,
        },
    )
    assert firmware.report(hub)["devices"][0]["installable"] is False


def test_an_update_entity_that_is_disabled_is_left_out():
    hub, _ = _hub_with_firmware()
    hub.entities["update.office_node_firmware"].disabled_by = "user"
    assert firmware.report(hub)["devices"] == []


def test_a_tasmota_device_without_an_update_entity_is_listed_from_its_own_version():
    hub, _ = make_hub(domain="tasmota")
    hub.devices["dev1"].sw_version = "15.6.0(release-tasmota)"
    row = firmware.report(hub)["devices"][0]
    assert row["entity_id"] is None
    assert row["device"] == "Test Device"
    assert row["installed_version"] == "15.6.0(release-tasmota)"
    assert row["available"] is False
    assert row["upgradable"] is True
    assert row["installable"] is False
    assert row["action"] == "upgrade"


def test_a_device_with_an_update_entity_is_not_listed_twice():
    hub, _entry = make_hub(domain="tasmota", entities=[{"entity_id": "update.node_firmware"}])
    hub.devices["dev1"].sw_version = "15.6.0"
    hub.publish("update.node_firmware", "off", {"supported_features": 1})
    rows = firmware.report(hub)["devices"]
    assert [r["device"] for r in rows] == ["Test Device"]
    assert rows[0]["source"] == "entity"


def test_a_running_install_is_counted():
    hub, _ = _hub_with_firmware()
    hub.publish(
        "update.office_node_firmware",
        "off",
        {
            "installed_version": "1.0",
            "latest_version": "2.0",
            "supported_features": 1,
            "in_progress": True,
            "update_percentage": 40,
        },
    )
    report = firmware.report(hub)
    assert report["installing"] == 1
    assert report["devices"][0]["progress"] == 40


@pytest.mark.asyncio
async def test_install_goes_through_the_hubs_update_service():
    hub, _ = _hub_with_firmware()
    calls = []

    async def call_service(domain, service, data=None, **kwargs):
        calls.append((domain, service, dict(data or {})))
        return {"changed_states": []}

    hub.call_service = call_service
    await firmware.install(hub, "update.office_node_firmware")
    assert calls == [("update", "install", {"entity_id": "update.office_node_firmware"})]


@pytest.mark.asyncio
async def test_install_needs_an_entity():
    hub, _ = _hub_with_firmware()
    with pytest.raises(DeviceUnavailable):
        await firmware.install(hub, "")


@pytest.mark.asyncio
async def test_tasmota_upgrade_asks_the_device_to_fetch_its_own_image():
    hub, entry = make_hub(domain="tasmota")
    hub.devices["dev1"].sw_version = "15.6.0"
    asked = []

    async def upgrade():
        asked.append(True)
        return {"Upgrade": "1"}

    hub.drivers[entry.entry_id] = SimpleNamespace(platform="tasmota", upgrade=upgrade)
    out = await firmware.upgrade_tasmota(hub, "dev1")
    assert asked == [True]
    assert out["reply"] == {"Upgrade": "1"}
    assert out["installed_version_before"] == "15.6.0"


@pytest.mark.asyncio
async def test_tasmota_upgrade_refuses_another_integration():
    hub, entry = make_hub(domain="esphome")
    hub.drivers[entry.entry_id] = SimpleNamespace(platform="esphome")
    with pytest.raises(DeviceUnavailable):
        await firmware.upgrade_tasmota(hub, "dev1")


@pytest.mark.asyncio
async def test_tasmota_upgrade_refuses_an_unknown_device():
    hub, _ = make_hub(domain="tasmota")
    with pytest.raises(DeviceUnavailable):
        await firmware.upgrade_tasmota(hub, "nope")
