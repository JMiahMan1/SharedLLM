"""Shared fixtures: a hub with one config entry and the entities a test names."""
from __future__ import annotations

import pytest

from services.homehub.core import Area, ConfigEntry, DeviceEntry, EntityEntry, Hub


def make_hub(domain: str = "test", data: dict | None = None, entities: list[dict] | None = None,
             options: dict | None = None, entry_id: str = "entry1") -> tuple[Hub, ConfigEntry]:
    """``entities`` items are EntityEntry kwargs; ``config_entry_id``/``platform`` default to the entry."""
    hub = Hub(time_zone="America/Phoenix", location={"latitude": 33.4, "longitude": -111.9, "elevation": 360})
    entry = ConfigEntry(entry_id=entry_id, domain=domain, title=f"{domain} test", data=data or {},
                        options=options or {})
    hub.config_entries[entry.entry_id] = entry
    hub.areas["office"] = Area("office", "Office")
    hub.devices["dev1"] = DeviceEntry(id="dev1", name="Test Device", area_id="office",
                                      config_entries=[entry.entry_id])
    for spec in entities or []:
        spec = dict(spec)
        spec.setdefault("platform", domain)
        spec.setdefault("config_entry_id", entry.entry_id)
        spec.setdefault("device_id", "dev1")
        ent = EntityEntry(**spec)
        hub.entities[ent.entity_id] = ent
    return hub, entry


@pytest.fixture
def hub_factory():
    return make_hub
