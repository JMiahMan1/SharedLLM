"""Managing what is set up: integrations (config entries) and their devices.

HA's Settings > Devices & services, for the hub's admins: list every entry
with its devices and entities, reconfigure an entry (a new address or login),
rename a device or move it to another area, and remove an entry or a device.

Removing an entry first releases the device's side of the pairing where the
protocol has one (HomeKit: our controller pairing is deleted from the
accessory, so it can be paired again), then stops the driver and deletes the
entry, its entities (and their last states) and every device left with no
entry. If the device cannot be reached to unpair, removal stops and says so;
``force`` removes it anyway (the accessory then needs a reset to pair again).

What an entry may be reconfigured with is read from its own data: the keys it
already has among ``RECONFIGURABLE``. Secrets are never sent back, only
whether they are set.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from services.homehub.core import ConfigEntry, DeviceEntry, HubError
from services.homehub.discovery import LABELS

log = logging.getLogger("homehub.entries")

#: Entry data keys an admin may change, with how the UI shows them.
RECONFIGURABLE: dict[str, dict[str, Any]] = {
    "host": {"label": "Address", "type": "text"},
    "url": {"label": "URL", "type": "text"},
    "port": {"label": "Port", "type": "text"},
    "username": {"label": "Username", "type": "text"},
    "password": {"label": "Password", "type": "password", "secret": True},
    "noise_psk": {"label": "Encryption key", "type": "password", "secret": True},
}
#: Entries that are infrastructure for many devices: removing one of their
#: devices must not remove the entry itself.
SHARED_ENTRIES = {"matter", "cast", "music_assistant", "mobile_app"}
_MATTER_NODE = re.compile(r"^deviceid_[0-9A-Fa-f]+-([0-9A-Fa-f]+)-MatterNodeDevice$")

EXTRA_LABELS = {"esphome_builder": "ESPHome Device Builder", "matter": "Matter", "music_assistant": "Music Assistant"}


class EntryError(HubError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def label(domain: str) -> str:
    return LABELS.get(domain) or EXTRA_LABELS.get(domain) or domain.replace("_", " ").title()


def reconfigure_fields(entry: ConfigEntry) -> list[dict[str, Any]]:
    out = []
    for key, meta in RECONFIGURABLE.items():
        if key not in (entry.data or {}):
            continue
        value = entry.data.get(key)
        field = {"key": key, "label": meta["label"], "type": meta["type"]}
        if meta.get("secret"):
            field["set"] = bool(value)
        else:
            current = entry.options.get(key) if key == "host" and entry.options.get("host") else value
            field["value"] = "" if current is None else str(current)
        out.append(field)
    return out


def summary(runtime: Any, entry: ConfigEntry) -> dict[str, Any]:
    hub = runtime.hub
    devices = [d for d in hub.devices.values() if entry.entry_id in d.config_entries]
    ents = [e for e in hub.entities.values() if e.config_entry_id == entry.entry_id]

    def dev_view(d: DeviceEntry) -> dict[str, Any]:
        return {"id": d.id, "name": d.display_name, "original_name": d.name, "name_by_user": d.name_by_user,
                "area_id": d.area_id, "manufacturer": d.manufacturer, "model": d.model,
                "via_device_id": d.via_device_id,
                "entities": sorted(e.entity_id for e in ents if e.device_id == d.id)}

    return {
        "entry_id": entry.entry_id, "domain": entry.domain, "label": label(entry.domain), "title": entry.title,
        "state": entry.state, "reason": entry.reason, "source": entry.source, "disabled_by": entry.disabled_by,
        "devices": sorted((dev_view(d) for d in devices), key=lambda d: d["name"].lower()),
        "entities": len(ents), "reconfigure": reconfigure_fields(entry),
        "unpairs": entry.domain in _unpair_hooks(), "shared": entry.domain in SHARED_ENTRIES,
        "syncs": entry.domain in _syncers(),
    }


def list_entries(runtime: Any) -> dict[str, Any]:
    hub = runtime.hub
    entries = [summary(runtime, e) for e in hub.config_entries.values() if e.source != "ignore"]
    entries.sort(key=lambda e: (e["label"].lower(), e["title"].lower()))
    areas = sorted(({"area_id": a.area_id, "name": a.name} for a in hub.areas.values()),
                   key=lambda a: a["name"].lower())
    return {"entries": entries, "areas": areas}


def _syncers() -> dict[str, Any]:
    from services.homehub import hardware  # hardware imports this module

    return hardware.SYNCERS


def _unpair_hooks() -> dict[str, Any]:
    from services.homehub import pairing

    return pairing.UNPAIR


def _entry(runtime: Any, entry_id: str) -> ConfigEntry:
    entry = runtime.hub.config_entries.get(entry_id)
    if entry is None:
        raise EntryError("No such integration entry", 404)
    return entry


# -- reconfigure --------------------------------------------------------------------------

async def reconfigure_entry(runtime: Any, entry_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """``{"title"?, "data"?: {...}}``: change the entry's title and the keys it already has, then reload it."""
    entry = _entry(runtime, entry_id)
    changes = body.get("data") or {}
    if not isinstance(changes, dict):
        raise EntryError("data must be an object")
    allowed = {f["key"] for f in reconfigure_fields(entry)}
    unknown = sorted(set(changes) - allowed)
    if unknown:
        raise EntryError(f"{label(entry.domain)} cannot change {', '.join(unknown)}"
                         + (f" (only {', '.join(sorted(allowed))})" if allowed else ""))
    data = dict(entry.data)
    for key, value in changes.items():
        text = "" if value is None else str(value).strip()
        if RECONFIGURABLE[key].get("secret") and not text:
            continue  # an empty secret field means "keep the stored one"
        if key in ("host", "url") and not text:
            raise EntryError(f"{RECONFIGURABLE[key]['label']} cannot be empty")
        if key == "port":
            if text and not text.isdigit():
                raise EntryError("Port must be a number")
            data[key] = int(text) if text else None
            continue
        data[key] = text if key not in ("password",) else str(value)
    title = str(body.get("title") or "").strip()
    if "title" in body and not title:
        raise EntryError("The name cannot be empty")
    options = dict(entry.options)
    if "host" in changes and str(changes.get("host") or "").strip():
        # An address override from the import would win over the new address.
        options.pop("host", None)
    old = (dict(entry.data), dict(entry.options), entry.title)
    entry.data, entry.options = data, options
    if title:
        entry.title = title
    try:
        runtime.store.save_config_entry(entry)
    except RuntimeError as exc:
        entry.data, entry.options, entry.title = old
        raise EntryError(str(exc), 503) from exc
    await runtime.stop_entry(entry_id)
    if not entry.disabled_by:
        runtime.start_entry(entry)
    log.info("reconfigured %s %s (%s)", entry.domain, entry.title, ", ".join(sorted(changes)) or "title")
    return summary(runtime, entry)


# -- devices ------------------------------------------------------------------------------

def update_device(runtime: Any, device_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """``{"name_by_user"?: str|null, "area_id"?: str|null}`` (an area by id or name)."""
    hub = runtime.hub
    dev = hub.devices.get(device_id)
    if dev is None:
        raise EntryError("No such device", 404)
    if "name_by_user" in body:
        name = body.get("name_by_user")
        dev.name_by_user = str(name).strip() or None if name is not None else None
    if "area_id" in body:
        area = body.get("area_id")
        if area in (None, ""):
            dev.area_id = None
        else:
            found = hub.area_id_by_name(str(area))
            if found is None:
                raise EntryError(f"There is no area {area!r}")
            dev.area_id = found
    runtime.store.save_device(dev)
    hub.bus.fire("device_registry_updated", {"action": "update", "device_id": dev.id})
    # Friendly names come from the device name: publish every entity again.
    for eid in hub.device_entities(dev.id):
        st = hub.states.get(eid)
        ent = hub.entities.get(eid)
        if st is not None and ent is not None:
            attrs = dict(st.attributes)
            attrs["friendly_name"] = hub.friendly_name(ent)
            hub.states.set(eid, st.state, attrs)
    return {"id": dev.id, "name": dev.display_name, "name_by_user": dev.name_by_user, "area_id": dev.area_id}


def _delete_row(store: Any, table: str, column: str, key: str) -> None:
    # The store has no per-row delete for these tables; table and column are fixed names here.
    store._exec(f"DELETE FROM {table} WHERE {column}=?", (key,))


def _drop_entities(runtime: Any, entity_ids: list[str]) -> None:
    hub, store = runtime.hub, runtime.store
    for eid in entity_ids:
        ent = hub.entities.pop(eid, None)
        hub.states.remove(eid)
        _delete_row(store, "entities", "entity_id", eid)
        _delete_row(store, "last_states", "entity_id", eid)
        for uuid_, mapped in list(hub.entity_registry_ids.items()):
            if mapped == eid:
                hub.entity_registry_ids.pop(uuid_, None)
        if ent is not None:
            hub.bus.fire("entity_registry_updated", {"action": "remove", "entity_id": eid})


def _drop_device(runtime: Any, dev: DeviceEntry) -> None:
    runtime.hub.devices.pop(dev.id, None)
    _delete_row(runtime.store, "devices", "id", dev.id)
    runtime.hub.bus.fire("device_registry_updated", {"action": "remove", "device_id": dev.id})


async def remove_entry(runtime: Any, entry_id: str, *, force: bool = False) -> dict[str, Any]:
    """Unpair (where the protocol has one), stop and delete an entry with its entities and devices."""
    hub = runtime.hub
    entry = _entry(runtime, entry_id)
    unpaired: bool | None = None
    warning = None
    hook = _unpair_hooks().get(entry.domain)
    if hook is not None:
        try:
            await hook(runtime, entry)
            unpaired = True
        except Exception as exc:
            unpaired = False
            warning = f"Could not unpair it from the device: {exc or type(exc).__name__}"
            if not force:
                raise EntryError(f"{warning}. Make sure it is on and reachable, or remove it anyway (it will then "
                                 "need a reset before it can be paired again).", 502) from exc
            log.warning("removing %s %s without unpairing: %s", entry.domain, entry.title, exc)
    await runtime.stop_entry(entry_id)
    ent_ids = [e.entity_id for e in hub.entities.values() if e.config_entry_id == entry_id]
    _drop_entities(runtime, ent_ids)
    removed_devices = []
    for dev in [d for d in hub.devices.values() if entry_id in d.config_entries]:
        dev.config_entries = [x for x in dev.config_entries if x != entry_id]
        if dev.config_entries:
            runtime.store.save_device(dev)
        else:
            _drop_device(runtime, dev)
            removed_devices.append(dev.id)
    runtime.store.delete_config_entry(entry_id)
    hub.config_entries.pop(entry_id, None)
    log.info("removed %s %s (%d entities, %d devices)", entry.domain, entry.title, len(ent_ids),
             len(removed_devices))
    out: dict[str, Any] = {"status": "removed", "entry_id": entry_id, "domain": entry.domain, "title": entry.title,
                           "entities": ent_ids, "devices": removed_devices, "unpaired": unpaired}
    if warning:
        out["warning"] = warning
    return out


async def remove_device(runtime: Any, device_id: str, *, force: bool = False) -> dict[str, Any]:
    """Remove one device. The entry's only top-level device takes the entry with it (and unpairs)."""
    hub = runtime.hub
    dev = hub.devices.get(device_id)
    if dev is None:
        raise EntryError("No such device", 404)
    entry_id = dev.config_entries[0] if dev.config_entries else None
    entry = hub.config_entries.get(entry_id or "")
    if entry is not None and entry.domain not in SHARED_ENTRIES:
        top = [d for d in hub.devices.values() if entry.entry_id in d.config_entries and not d.via_device_id]
        if dev.via_device_id is None and [d.id for d in top] == [dev.id]:
            return {**await remove_entry(runtime, entry.entry_id, force=force), "device_id": device_id}
    if entry is not None and entry.domain == "matter":
        await _remove_matter_node(runtime, entry, dev, force=force)
    children = [d for d in hub.devices.values() if d.via_device_id == dev.id]
    ent_ids = [eid for d in [dev, *children] for eid in hub.device_entities(d.id)]
    _drop_entities(runtime, ent_ids)
    for d in [dev, *children]:
        _drop_device(runtime, d)
    if entry is not None:
        drv = hub.drivers.get(entry.entry_id)
        if drv is not None and hasattr(drv, "reindex"):
            try:
                drv.reindex()
            except Exception:
                log.exception("reindexing %s after removing a device failed", entry.title)
    log.info("removed device %s (%d entities)", dev.display_name, len(ent_ids))
    return {"status": "removed", "device_id": device_id, "entry_id": entry_id, "entities": ent_ids,
            "devices": [d.id for d in [dev, *children]]}


async def _remove_matter_node(runtime: Any, entry: ConfigEntry, dev: DeviceEntry, *, force: bool) -> None:
    """Take a node off the fabric through the Matter Server (``remove_node``), as HA's "Delete" does."""
    node_id = None
    for pair in dev.identifiers:
        m = _MATTER_NODE.match(str(pair[1])) if len(pair) >= 2 else None
        if m:
            node_id = int(m.group(1), 16)
    driver = runtime.hub.drivers.get(entry.entry_id)
    try:
        if node_id is None:
            raise HubError("the device has no Matter node id")
        if driver is None:
            raise HubError("the Matter Server is not connected")
        await driver._send("remove_node", node_id=node_id)
        getattr(driver, "nodes", {}).pop(node_id, None)
    except Exception as exc:
        if not force:
            raise EntryError(f"Could not remove it from the Matter fabric: {exc}. Make sure the Matter Server is up, "
                             "or remove it anyway (the device then needs a factory reset).", 502) from exc
        log.warning("removing Matter device %s without decommissioning: %s", dev.display_name, exc)


# -- the ESPHome Device Builder -----------------------------------------------------------

def esphome_builder(runtime: Any) -> dict[str, Any]:
    """Where admins open the builder, when an entry says so; never its login."""
    entry = next((e for e in runtime.hub.config_entries.values()
                  if e.domain == "esphome_builder" and not e.disabled_by), None)
    if entry is None:
        return {"configured": False}
    return {"configured": True, "entry_id": entry.entry_id, "title": entry.title, "url": entry.data.get("url"),
            "state": entry.state, "reason": entry.reason, "login": bool(entry.data.get("username"))}
