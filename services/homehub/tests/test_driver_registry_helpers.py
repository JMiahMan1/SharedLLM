"""Driver.ensure_device / ensure_entity: an integration registers the entities it owns, as HA's do."""
from __future__ import annotations

from services.homehub.core import Driver, EntityEntry
from services.homehub.tests.conftest import make_hub


class Saved:
    def __init__(self) -> None:
        self.devices: list[str] = []
        self.entities: list[str] = []

    def save_device(self, dev) -> None:
        self.devices.append(dev.id)

    def save_entity(self, ent) -> None:
        self.entities.append(ent.entity_id)


def _driver(entities=None):
    hub, entry = make_hub("demo", entities=entities)
    hub.store = Saved()
    return hub, Driver(hub, entry)


def test_a_missing_entity_is_registered_on_the_entrys_device_and_saved():
    hub, drv = _driver()
    events = []
    hub.bus.subscribe(lambda t, d: events.append((t, d)), "entity_registry_updated")
    dev = drv.ensure_device("Test Device")
    assert dev.id == "dev1"  # the entry's one device is reused
    ent = drv.ensure_entity("remote", "AA:BB", None, device=dev)
    assert ent.entity_id == "remote.test_device"
    assert ent.platform == "demo" and ent.config_entry_id == "entry1" and ent.device_id == "dev1"
    assert hub.entities["remote.test_device"] is ent
    assert hub.store.entities == ["remote.test_device"]
    assert events == [("entity_registry_updated", {"action": "create", "entity_id": "remote.test_device"})]


def test_an_existing_entity_is_never_replaced_even_when_disabled():
    hub, drv = _driver([{"entity_id": "remote.mine", "unique_id": "aa:bb", "disabled_by": "user"}])
    ent = drv.ensure_entity("remote", "AA:BB", None)
    assert ent.entity_id == "remote.mine" and ent.disabled_by == "user"
    assert hub.store.entities == []


def test_a_taken_entity_id_gets_a_suffix_and_info_is_confirmed():
    hub, drv = _driver([{"entity_id": "sensor.test_device_power", "unique_id": "other"}])
    ent = drv.ensure_entity("sensor", "plug_power", "Power", device=hub.devices["dev1"],
                            original_device_class="power", unit_of_measurement="W")
    assert ent.entity_id == "sensor.test_device_power_2"
    assert ent.original_name == "Power" and ent.has_entity_name
    assert ent.original_device_class == "power" and ent.unit_of_measurement == "W"
    assert {"original_device_class", "unit_of_measurement", "original_name"} <= set(ent.confirmed)


def test_a_device_is_created_when_the_entry_has_none_and_matched_by_connection_later():
    hub, drv = _driver()
    hub.devices["dev1"].config_entries = ["someone-else"]
    dev = drv.ensure_device("Bedroom TV", connections=[["mac", "aa:bb:cc:dd:ee:ff"]], manufacturer="Google")
    assert dev.id != "dev1" and dev.name == "Bedroom TV" and dev.config_entries == ["entry1"]
    assert dev.manufacturer == "Google" and ["mac", "aa:bb:cc:dd:ee:ff"] in dev.connections
    again = drv.ensure_device("Bedroom TV", connections=[["mac", "aa:bb:cc:dd:ee:ff"]])
    assert again is dev
    assert hub.store.devices.count(dev.id) >= 1


def test_disabled_registers_the_entity_disabled_by_the_integration():
    hub, drv = _driver()
    ent = drv.ensure_entity("sensor", "x_rssi", "Signal", device=hub.devices["dev1"], disabled=True)
    assert ent.disabled_by == "integration"
    assert ent not in drv.entities
    assert isinstance(ent, EntityEntry)


def test_another_integrations_device_with_the_same_mac_is_joined_as_ha_merges_them():
    hub, drv = _driver()
    hub.devices["dev1"].config_entries = ["cast-entry"]
    hub.devices["dev1"].connections = [["mac", "aa:bb:cc:dd:ee:ff"]]
    dev = drv.ensure_device("Bedroom TV", connections=[["mac", "aa:bb:cc:dd:ee:ff"]])
    assert dev.id == "dev1" and dev.config_entries == ["cast-entry", "entry1"]
    assert set(hub.store.devices) == {"dev1"}
