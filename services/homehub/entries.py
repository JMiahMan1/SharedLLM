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

from services.homehub.core import UNAVAILABLE, Area, ConfigEntry, DeviceEntry, HubError
from services.homehub.discovery import LABELS

log = logging.getLogger("homehub.entries")

#: Entry data keys an admin may change, with how the UI shows them.
RECONFIGURABLE: dict[str, dict[str, Any]] = {
    "host": {"label": "Address", "type": "text"},
    "url": {"label": "URL", "type": "text"},
    # A camera set up by its web page keeps these (in its options, as HA's MJPEG integration did).
    "mjpeg_url": {"label": "Stream URL", "type": "text"},
    "still_image_url": {"label": "Snapshot URL", "type": "text"},
    "port": {"label": "Port", "type": "text"},
    "username": {"label": "Username", "type": "text"},
    "password": {"label": "Password", "type": "password", "secret": True},
    "noise_psk": {"label": "Encryption key", "type": "password", "secret": True},
}
#: Fields offered for every entry of these integrations, even one that does not keep them yet: an entry
#: imported from HA kept a Tasmota device's address only on its device (``configuration_url``) and had no
#: password. Per field, what the UI shows instead of the generic label and help.
DOMAIN_FIELDS: dict[str, dict[str, dict[str, Any]]] = {
    "tasmota": {
        "host": {"help": "The device's IP address. The hub follows it by its MAC when the router changes it."},
        "password": {"label": "Web password",
                     "help": "The password of the device's own web page. Leave empty to keep the stored one."},
    },
}
#: Entries that are infrastructure for many devices: removing one of their
#: devices must not remove the entry itself.
SHARED_ENTRIES = {"matter", "cast", "music_assistant", "mobile_app"}
_MATTER_NODE = re.compile(r"^deviceid_[0-9A-Fa-f]+-([0-9A-Fa-f]+)-MatterNodeDevice$")

EXTRA_LABELS = {"jarvis_identity": "Jarvis accounts", "esphome_builder": "ESPHome Device Builder", "matter": "Matter", "music_assistant": "Music Assistant",
                "group": "Light group"}


class EntryError(HubError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def label(domain: str) -> str:
    return LABELS.get(domain) or EXTRA_LABELS.get(domain) or domain.replace("_", " ").title()


#: Keys an entry may keep in its options rather than its data; they are changed where they are.
OPTION_KEYS = {"mjpeg_url", "still_image_url", "username", "password"}

#: The options each integration's driver reads (``PATCH /hub/entries/{id}/options``). A key outside its
#: integration's set is refused unless the entry already has it (an option HA's import carried): a typo
#: would otherwise be stored and do nothing, and the entry restarted for it.
ENTRY_OPTIONS: dict[str, frozenset[str]] = {
    "androidtv_remote": frozenset({"host", "mac", "cert_file", "key_file"}),
    "cast": frozenset({"known_hosts"}),
    "dlna_dmr": frozenset({"url"}),
    "esphome": frozenset({"host", "allow_service_calls"}),
    "group": frozenset({"group_type", "entities", "all", "hide_members"}),
    "matter": frozenset({"url"}),
    "mjpeg": frozenset({"mjpeg_url", "still_image_url", "username", "password", "authentication", "verify_ssl"}),
    "mobile_app": frozenset({"jarvis_user", "jarvis_fallback"}),
    "music_assistant": frozenset({"url", "token"}),
    "reolink": frozenset({"host", "protocol"}),
    "roku": frozenset({"host"}),
    "samsungtv": frozenset({"host", "mac"}),
    "srp_energy": frozenset({"time_zone", "statistics", "statistics_backfill_days", "tou_backfill_days"}),
    "switch_as_x": frozenset({"entity_id", "target_domain", "invert", "state_entity_id", "momentary",
                              "device_class", "travel_s"}),
    "tasmota": frozenset({"host"}),
    "webostv": frozenset({"host", "mac"}),
    "wiz": frozenset({"host"}),
}


def check_option_keys(entry: ConfigEntry, patch: dict[str, Any]) -> None:
    """Refuse an options patch naming a key the entry's integration does not read (``EntryError`` 400)."""
    allowed = ENTRY_OPTIONS.get(entry.domain, frozenset()) | set(entry.options or {})
    unknown = sorted(k for k in patch if k not in allowed)
    if unknown:
        known = ", ".join(sorted(allowed)) or "none"
        raise EntryError(f"{label(entry.domain)} has no option {', '.join(unknown)} (its options: {known})")


def _home_of(entry: ConfigEntry, key: str) -> str | None:
    """Where the entry keeps ``key``: "data", "options" (only for OPTION_KEYS), or None."""
    if key in (entry.data or {}):
        return "data"
    if key in OPTION_KEYS and key in (entry.options or {}):
        return "options"
    return None


#: Setup fields that identify what was paired rather than how to reach it: changing one would
#: describe another device or account, so it is set up again instead.
PAIRING_ONLY: dict[str, set[str]] = {"matter": {"method", "code"}, "roborock": {"email", "username"}}


def _spec_fields(entry: ConfigEntry, hub: Any = None) -> dict[str, dict[str, Any]]:
    """The integration's own setup fields (manual_flows.SPECS), so each can be changed after setup."""
    from services.homehub.manual_flows import SPECS  # manual_flows imports this module's neighbours

    spec = SPECS.get(entry.domain) or {}
    fields = spec.get("fields") or []
    if callable(fields):
        if hub is None:
            return {}
        fields = fields(hub)
    skip = PAIRING_ONLY.get(entry.domain, set())
    out: dict[str, dict[str, Any]] = {}
    for f in fields:
        key = str(f.get("key") or "")
        if not key or key in skip:
            continue
        meta = {"label": f.get("label") or key, "type": f.get("type") or "text",
                "secret": f.get("type") == "password"}
        if f.get("options"):
            meta["options"] = f["options"]
        if f.get("help"):
            meta["help"] = f["help"]
        out[key] = meta
    return out


def _field_meta(entry: ConfigEntry, key: str, hub: Any = None) -> dict[str, Any] | None:
    if key in DOMAIN_FIELDS.get(entry.domain, {}):
        return {**RECONFIGURABLE[key], **DOMAIN_FIELDS[entry.domain][key]}
    return RECONFIGURABLE.get(key) or _spec_fields(entry, hub).get(key)


def device_address(hub: Any, entry: ConfigEntry) -> str | None:
    """The address on the entry's one device (``configuration_url``), for an entry that keeps none itself."""
    if hub is None:
        return None
    from urllib.parse import urlparse

    devices = [d for d in hub.devices.values() if entry.entry_id in (d.config_entries or [])]
    urls = {str(d.configuration_url) for d in devices if d.configuration_url}
    if len(urls) != 1:
        return None
    parts = urlparse(urls.pop())
    if not parts.hostname:
        return None
    return parts.hostname + (f":{parts.port}" if parts.port else "")


def needs_of(entry: ConfigEntry) -> str | None:
    """What a failing entry needs from an admin: the ``needs`` of the AuthRequired that parked it
    ("password", "login", "pairing", "bindkey"); "password" for an older AuthRequired reason."""
    if entry.disabled_by or entry.state == "loaded":
        return None
    if entry.needs:
        return entry.needs
    return "password" if str(entry.reason or "").startswith("AuthRequired") else None


def reconfigure_fields(entry: ConfigEntry, hub: Any = None) -> list[dict[str, Any]]:
    """What an admin may change: the generic keys the entry has, then its integration's setup fields."""
    out = []
    seen: set[str] = set()
    own = DOMAIN_FIELDS.get(entry.domain, {})
    candidates = [(k, {**RECONFIGURABLE[k], **extra}) for k, extra in own.items()]
    candidates += [(k, m) for k, m in RECONFIGURABLE.items() if _home_of(entry, k) is not None
                   and not (own and k == "username")]  # Tasmota's web user is always admin
    candidates += [(k, m) for k, m in _spec_fields(entry, hub).items() if k not in RECONFIGURABLE]
    for key, meta in candidates:
        if key in seen:
            continue
        seen.add(key)
        home = _home_of(entry, key) or "data"
        value = (entry.data if home == "data" else entry.options).get(key)
        field: dict[str, Any] = {"key": key, "label": meta["label"], "type": meta["type"]}
        for extra in ("options", "help"):
            if meta.get(extra):
                field[extra] = meta[extra]
        if meta.get("secret"):
            field["set"] = bool(value)
        else:
            current = entry.options.get(key) if key == "host" and entry.options.get("host") else value
            if key == "host" and not current and key in own:
                current = device_address(hub, entry)
            field["value"] = "" if current is None else ("1" if current is True else "" if current is False
                                                          else str(current))
        out.append(field)
    return out


def offline_since(runtime: Any, entry: ConfigEntry) -> str | None:
    """When an entry that is not answering last answered, as ISO time; None while it works.

    Its entities' "unavailable" states keep the time they turned unavailable across hub
    restarts (they are restored, then set to the same state), so that is the first
    choice; an entry with none falls back to when this run of the hub saw it fail.
    """
    if entry.state == "loaded" or entry.disabled_by:
        return None
    hub = runtime.hub
    gone = [s.last_changed for e in hub.entities.values() if e.config_entry_id == entry.entry_id
            and (s := hub.states.get(e.entity_id)) is not None and s.state == UNAVAILABLE]
    when = max(gone) if gone else getattr(runtime, "failing_since", {}).get(entry.entry_id)
    return when.isoformat() if when is not None else None


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
        "offline_since": offline_since(runtime, entry), "needs": needs_of(entry),
        "devices": sorted((dev_view(d) for d in devices), key=lambda d: d["name"].lower()),
        "entities": len(ents), "reconfigure": reconfigure_fields(entry, hub),
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

async def _check_changes(domain: str, changes: dict[str, Any], data: dict[str, Any]) -> None:
    """A new account key is checked as the setup flow checked it (manual_flows.RECONFIGURE_CHECKS)."""
    from services.homehub.flows import NeedsInput
    from services.homehub.manual_flows import RECONFIGURE_CHECKS

    found = RECONFIGURE_CHECKS.get(domain)
    if found is None:
        return
    keys, check = found
    if not any(str(changes.get(k) or "").strip() for k in keys):
        return  # nothing it checks was changed (an empty secret keeps the stored one)
    try:
        await check(data)
    except NeedsInput as exc:
        raise EntryError(exc.error or exc.message) from exc


async def reconfigure_entry(runtime: Any, entry_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """``{"title"?, "data"?: {...}}``: change the entry's title and the keys it already has, then reload it."""
    entry = _entry(runtime, entry_id)
    changes = body.get("data") or {}
    if not isinstance(changes, dict):
        raise EntryError("data must be an object")
    allowed = {f["key"] for f in reconfigure_fields(entry, runtime.hub)}
    unknown = sorted(set(changes) - allowed)
    if unknown:
        raise EntryError(f"{label(entry.domain)} cannot change {', '.join(unknown)}"
                         + (f" (only {', '.join(sorted(allowed))})" if allowed else ""))
    data = dict(entry.data)
    kept_options = dict(entry.options)
    for key, value in changes.items():
        text = "" if value is None else str(value).strip()
        target = kept_options if _home_of(entry, key) == "options" else data
        meta = _field_meta(entry, key, runtime.hub) or {"label": key}
        if meta.get("secret") and not text:
            continue  # an empty secret field means "keep the stored one"
        if key in ("host", "url", "mjpeg_url") and not text:
            raise EntryError(f"{meta['label']} cannot be empty")
        if key in ("url", "mjpeg_url", "still_image_url") and text and not text.lower().startswith(("http://", "https://", "rtsp://", "rtsps://")):
            raise EntryError(f"{meta['label']} must start with http:// or https://")
        if meta.get("options") and text not in {str(o.get("value")) for o in meta["options"]}:
            raise EntryError(f"{meta['label']}: choose one of the offered values")
        if isinstance(entry.data.get(key), bool):
            data[key] = text in ("1", "true", "yes")  # a yes/no setting (SRP's time-of-use plan)
            continue
        if key == "port":
            if text and not text.isdigit():
                raise EntryError("Port must be a number")
            data[key] = int(text) if text else None
            continue
        target[key] = str(value) if meta.get("secret") else text
    title = str(body.get("title") or "").strip()
    if "title" in body and not title:
        raise EntryError("The name cannot be empty")
    options = kept_options
    if "host" in changes and str(changes.get("host") or "").strip():
        # An address override from the import would win over the new address.
        options.pop("host", None)
    if entry.domain in DOMAIN_FIELDS:
        # Kept on the entry from now on, where address_follow updates it when DHCP moves the device.
        if not data.get("host") and not options.get("host") and (where := device_address(runtime.hub, entry)):
            data["host"] = where
        if data.get("password") and not data.get("username"):
            data["username"] = "admin"  # Tasmota's web user is always admin
    await _check_changes(entry.domain, changes, data)
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
    """``{"name_by_user"?: str|null, "area_id"?: str|null, "labels"?: [label ids], "hidden"?: bool}``.

    An area by id or name. ``hidden`` hides the device from what people and Jarvis browse (duplicates.py).
    """
    from services.homehub import duplicates, registries

    hub = runtime.hub
    dev = hub.devices.get(device_id)
    if dev is None:
        raise EntryError("No such device", 404)
    if "hidden" in body and not isinstance(body["hidden"], bool):
        raise EntryError("hidden must be true or false")
    if "hidden" in body and bool(dev.hidden_by) != body["hidden"] and duplicates.record_of(runtime, dev.id):
        raise EntryError("This device is combined with its other copy. Uncombine it first.", 409)
    labels = registries.clean_label_ids(runtime, body["labels"]) if "labels" in body else None
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
    if labels is not None:
        dev.labels = labels
    runtime.store.save_device(dev)
    hub.bus.fire("device_registry_updated", {"action": "update", "device_id": dev.id})
    if "hidden" in body:
        duplicates.set_device_hidden(runtime, dev.id, body["hidden"])
    # Friendly names come from the device name: publish every entity again.
    for eid in hub.device_entities(dev.id):
        st = hub.states.get(eid)
        ent = hub.entities.get(eid)
        if st is not None and ent is not None:
            attrs = dict(st.attributes)
            attrs["friendly_name"] = hub.friendly_name(ent)
            hub.states.set(eid, st.state, attrs)
    return {"id": dev.id, "name": dev.display_name, "name_by_user": dev.name_by_user, "area_id": dev.area_id,
            "labels": list(dev.labels), "hidden": bool(dev.hidden_by), "hidden_by": dev.hidden_by}


def _delete_row(store: Any, table: str, column: str, key: str) -> None:
    # The store has no per-row delete for these tables; table and column are fixed names here.
    store._exec(f"DELETE FROM {table} WHERE {column}=?", (key,))


def _drop_entities(runtime: Any, entity_ids: list[str]) -> None:
    hub, store = runtime.hub, runtime.store
    mapping_changed = False
    for eid in entity_ids:
        ent = hub.entities.pop(eid, None)
        hub.states.remove(eid)
        _delete_row(store, "entities", "entity_id", eid)
        _delete_row(store, "last_states", "entity_id", eid)
        for uuid_, mapped in list(hub.entity_registry_ids.items()):
            if mapped == eid:
                hub.entity_registry_ids.pop(uuid_, None)
                mapping_changed = True
        if ent is not None:
            hub.bus.fire("entity_registry_updated", {"action": "remove", "entity_id": eid})
    if mapping_changed and store is not None:
        # The stored copy is what the next start loads; without this a removed entity's id came back.
        store.set_kv("entity_registry_ids", hub.entity_registry_ids)


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
    if entry.domain == "switch_as_x":
        _show_wrapped_switch(runtime, entry)
    log.info("removed %s %s (%d entities, %d devices)", entry.domain, entry.title, len(ent_ids),
             len(removed_devices))
    out: dict[str, Any] = {"status": "removed", "entry_id": entry_id, "domain": entry.domain, "title": entry.title,
                           "entities": ent_ids, "devices": removed_devices, "unpaired": unpaired}
    if warning:
        out["warning"] = warning
    return out


def _show_wrapped_switch(runtime: Any, entry: ConfigEntry) -> None:
    """A removed switch_as_x shows its switch again, unless another wrapper or a person hid it."""
    hub = runtime.hub
    source = str({**entry.data, **entry.options}.get("entity_id") or "")
    ent = hub.entities.get(source)
    if ent is None or ent.hidden_by != "integration":
        return
    if any(e.domain == "switch_as_x" and {**e.data, **e.options}.get("entity_id") == source
           for e in hub.config_entries.values()):
        return
    ent.hidden_by = None
    runtime.store.save_entity(ent)
    hub.bus.fire("entity_registry_updated", {"action": "update", "entity_id": source})


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


# -- areas ------------------------------------------------------------------------------------
# A hub that never imported anything starts with no areas; these make and keep them. The Home Assistant import
# may also have brought some (names a person gave): they are edited the same way.

def _area_view(area: Area) -> dict[str, Any]:
    return {"area_id": area.area_id, "name": area.name, "aliases": list(area.aliases), "icon": area.icon,
            "floor_id": area.floor_id, "labels": list(area.labels)}


def _area_fields(body: dict[str, Any], runtime: Any) -> dict[str, Any]:
    from services.homehub import registries

    unknown = sorted(set(body) - {"name", "aliases", "icon", "floor_id", "labels"})
    if unknown:
        raise EntryError(f"An area cannot have {', '.join(unknown)}")
    out: dict[str, Any] = {}
    if "name" in body:
        name = str(body.get("name") or "").strip()
        if not name:
            raise EntryError("An area needs a name")
        out["name"] = name
    if "aliases" in body:
        aliases = body.get("aliases") or []
        if not isinstance(aliases, list):
            raise EntryError("aliases is a list of names")
        out["aliases"] = [str(a).strip() for a in aliases if str(a).strip()]
    for key in ("icon", "floor_id"):
        if key in body:
            out[key] = str(body[key]).strip() or None if body[key] is not None else None
    if "floor_id" in out:
        registries.check_floor(runtime, out["floor_id"])
    if "labels" in body:
        out["labels"] = registries.clean_label_ids(runtime, body["labels"])
    return out


def _name_taken(hub: Any, name: str, except_id: str | None = None) -> bool:
    return any(a.name.lower() == name.lower() and a.area_id != except_id for a in hub.areas.values())


def list_areas(runtime: Any) -> dict[str, Any]:
    return {"areas": sorted((_area_view(a) for a in runtime.hub.areas.values()), key=lambda a: a["name"].lower())}


def create_area(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    """``{"name", "aliases"?, "icon"?, "floor_id"?}``; the id is made from the name (``living_room``)."""
    hub = runtime.hub
    fields = _area_fields(body, runtime)
    if "name" not in fields:
        raise EntryError("An area needs a name")
    if _name_taken(hub, fields["name"]):
        raise EntryError(f"There is already an area named {fields['name']!r}", 409)
    base = re.sub(r"[^a-z0-9]+", "_", fields["name"].lower()).strip("_") or "area"
    area_id, n = base, 2
    while area_id in hub.areas:
        area_id, n = f"{base}_{n}", n + 1
    area = Area(area_id=area_id, **fields)
    runtime.store.save_area(area)
    hub.areas[area_id] = area
    hub.bus.fire("area_registry_updated", {"action": "create", "area_id": area_id})
    return _area_view(area)


def update_area(runtime: Any, area_id: str, body: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    area = hub.areas.get(area_id)
    if area is None:
        raise EntryError("No such area", 404)
    fields = _area_fields(body, runtime)
    if "name" in fields and _name_taken(hub, fields["name"], area_id):
        raise EntryError(f"There is already an area named {fields['name']!r}", 409)
    for key, value in fields.items():
        setattr(area, key, value)
    runtime.store.save_area(area)
    hub.bus.fire("area_registry_updated", {"action": "update", "area_id": area_id})
    return _area_view(area)


def delete_area(runtime: Any, area_id: str) -> dict[str, Any]:
    """Remove an area; its devices and entities keep working, with no area."""
    hub = runtime.hub
    if area_id not in hub.areas:
        raise EntryError("No such area", 404)
    devices, ents = [], []
    for dev in hub.devices.values():
        if dev.area_id == area_id:
            dev.area_id = None
            runtime.store.save_device(dev)
            devices.append(dev.id)
    for ent in hub.entities.values():
        if ent.area_id == area_id:
            ent.area_id = None
            runtime.store.save_entity(ent)
            ents.append(ent.entity_id)
    hub.areas.pop(area_id, None)
    _delete_row(runtime.store, "areas", "area_id", area_id)
    hub.bus.fire("area_registry_updated", {"action": "remove", "area_id": area_id})
    return {"status": "removed", "area_id": area_id, "devices": devices, "entities": ents}


# -- accounts other services read ------------------------------------------------------------

def integration_data(runtime: Any, domain: str) -> dict[str, Any]:
    """The enabled entry of a one-per-hub account, with its data (secrets too: internal callers only).

    ``configured`` is false when there is none, or it is disabled: telemetry then treats the
    check it drives as not set up.
    """
    entry = next((e for e in runtime.hub.config_entries.values()
                  if e.domain == domain and e.source != "ignore" and not e.disabled_by), None)
    if entry is None:
        return {"domain": domain, "configured": False, "data": {}}
    return {"domain": domain, "configured": True, "entry_id": entry.entry_id, "title": entry.title,
            "state": entry.state, "data": dict(entry.data or {})}


# -- the ESPHome Device Builder -----------------------------------------------------------

def esphome_builder(runtime: Any) -> dict[str, Any]:
    """Where admins open the builder, when an entry says so; never its login."""
    entry = next((e for e in runtime.hub.config_entries.values()
                  if e.domain == "esphome_builder" and not e.disabled_by), None)
    from services.homehub import esphome_configs

    if entry is None:  # the config report does not depend on the dashboard link
        return {"configured": False, "configs": esphome_configs.report(runtime.hub)}
    return {"configured": True, "entry_id": entry.entry_id, "title": entry.title, "url": entry.data.get("url"),
            "state": entry.state, "reason": entry.reason, "login": bool(entry.data.get("username")),
            "configs": esphome_configs.report(runtime.hub)}
