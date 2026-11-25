"""Every device's firmware in one list: what it runs, what is offered, and how to install it.

Devices report firmware in different ways, so the list is assembled from both
places the hub already keeps it:

* an ``update`` entity, which carries ``installed_version``/``latest_version``
  and, when the integration can install, the install feature bit. ESPHome nodes
  (through the Device Builder), Reolink, Echos and Matter devices all publish one.
* a device's own ``sw_version`` where no update entity exists. Tasmota is the
  case that matters: it knows its version and can fetch a new image from its own
  update address, but it exposes no update entity to ask.

Nothing here decides whether a newer version exists on its own: the hub already
filters every update entity through ``update_state``/``is_newer_version`` so an
older image is never offered (services/homehub/core.py), and this list reads the
result of that.
"""

from __future__ import annotations

from typing import Any

from services.homehub.core import UPDATE_INSTALL, DeviceUnavailable, is_newer_version

#: The integrations a device's own ``sw_version`` is enough to update from without an update entity.
SELF_UPDATING = ("tasmota",)


def _entry_domain(hub: Any, device_id: str | None) -> tuple[str | None, str | None]:
    """The device's first integration: its domain and the entry's title."""
    dev = hub.devices.get(device_id or "")
    if dev is None:
        return None, None
    for entry_id in dev.config_entries:
        entry = hub.config_entries.get(entry_id)
        if entry is not None:
            return entry.domain, entry.title
    return None, None


def _entity_name(ent: Any, dev: Any) -> str:
    if ent.name:
        return str(ent.name)
    if ent.original_name:
        return str(ent.original_name)
    if dev is not None and dev.display_name:
        return "Firmware"
    return "Firmware"


def report(hub: Any) -> dict[str, Any]:
    """``{"devices": [...], "installing": n}``, one row per updateable thing, newest offer first.

    A row is a device plus the update entity that speaks for its firmware, so a
    device offering two (an ESPHome node with its own ``update`` block and the
    Builder's Firmware entity) appears twice, named after the entity: both are
    real, and neither should hide the other.
    """
    rows: list[dict[str, Any]] = []
    covered: set[str] = set()
    for ent in list(hub.entities.values()):
        if ent.domain != "update" or ent.disabled_by or ent.hidden_by:
            continue
        dev = hub.devices.get(ent.device_id or "")
        st = hub.states.get(ent.entity_id)
        attrs: dict[str, Any] = dict(st.attributes or {}) if st is not None else {}
        installed = attrs.get("installed_version")
        latest = attrs.get("latest_version")
        domain, entry_title = _entry_domain(hub, ent.device_id)
        state = str(st.state).lower() if st is not None else "unknown"
        features = attrs.get("supported_features")
        if features is None:
            features = getattr(ent, "supported_features", 0) or 0
        rows.append(
            {
                "id": ent.entity_id,
                "entity_id": ent.entity_id,
                "device_id": ent.device_id,
                "device": (dev.display_name if dev is not None else None) or entry_title or ent.device_id,
                "name": _entity_name(ent, dev),
                "integration": domain,
                "model": getattr(dev, "model", None),
                "area_id": (dev.area_id if dev is not None else None) or ent.area_id,
                "installed_version": installed,
                "latest_version": latest,
                "available": state == "on",
                "installable": bool(int(features) & UPDATE_INSTALL),
                "in_progress": bool(attrs.get("in_progress")),
                "progress": attrs.get("update_percentage"),
                "skipped_version": attrs.get("skipped_version"),
                "release_url": attrs.get("release_url"),
                "release_summary": attrs.get("release_summary"),
                "source": "entity",
                "action": "install",
            }
        )
        if ent.device_id:
            covered.add(ent.device_id)

    for dev in list(hub.devices.values()):
        if dev.id in covered or dev.disabled_by or dev.hidden_by:
            continue
        domain, entry_title = _entry_domain(hub, dev.id)
        if domain not in SELF_UPDATING:
            continue
        rows.append(
            {
                "id": dev.id,
                "entity_id": None,
                "device_id": dev.id,
                "device": dev.display_name or entry_title or dev.id,
                "name": "Firmware",
                "integration": domain,
                "model": dev.model,
                "area_id": dev.area_id,
                "installed_version": dev.sw_version,
                "latest_version": None,
                "available": False,
                "upgradable": bool(dev.sw_version),
                "installable": False,
                "in_progress": False,
                "progress": None,
                "skipped_version": None,
                "release_url": None,
                "release_summary": None,
                "source": "device",
                "action": "upgrade",
            }
        )

    rows.sort(key=lambda r: (not r["available"], str(r["device"] or "").lower(), str(r["name"]).lower()))
    return {"devices": rows, "installing": sum(1 for r in rows if r["in_progress"])}


async def install(hub: Any, entity_id: str) -> dict[str, Any]:
    """Install the offered version on one update entity, through the hub's own update service."""
    if not entity_id:
        raise DeviceUnavailable("firmware: install needs an entity_id")
    result = await hub.call_service("update", "install", {"entity_id": entity_id})
    return {"entity_id": entity_id, "started": True, "changed": len(result.get("changed_states") or [])}


async def upgrade_tasmota(hub: Any, device_id: str) -> dict[str, Any]:
    """Tell a Tasmota device to fetch and flash the image at its own update address.

    Tasmota has no update entity and no version to check against: the device
    keeps an update address (``OtaUrl``) and ``Upgrade 1`` downloads from it.
    The reply is the device's own answer, returned as it came.
    """
    dev = hub.devices.get(device_id)
    if dev is None:
        raise DeviceUnavailable(f"firmware: no device {device_id}")
    for entry_id in dev.config_entries:
        driver = hub.drivers.get(entry_id)
        if driver is not None and getattr(driver, "platform", "") == "tasmota":
            before = dev.sw_version
            reply = await driver.upgrade()
            return {"device_id": device_id, "installed_version_before": before, "reply": reply}
    raise DeviceUnavailable(f"firmware: {dev.display_name or device_id} is not a Tasmota device the hub runs")


def newer(latest: Any, installed: Any) -> bool:
    """Whether ``latest`` is a real update on ``installed`` (exposed for the UI's tests and callers)."""
    return is_newer_version(latest, installed)
