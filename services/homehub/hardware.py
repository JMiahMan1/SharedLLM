"""The hardware list: HA's Settings > Devices & services, built from the live registry.

Everything here is computed from what the hub holds right now: the config
entries and their runtime state, the device and entity registries, the state
machine and discovery's findings. Nothing is a static list of devices.

- ``integrations(runtime)``: one row per integration (domain): its entries with
  their state and the error message, device/entity/unavailable counts, how HA
  discovers it, whether it can be added by hand, and how many new devices of it
  discovery is offering. Every integration the hub has a driver for is listed,
  set up or not, so "Add" has somewhere to start.
- ``device_list(runtime)``: every device with manufacturer, model, firmware,
  area, integration, connections (MAC, IP), availability and last seen.
- ``device_detail(runtime, id)``: one device with its entities, their current
  states (in the shape the Rooms page's controls take) and registry settings.

Changes, as HA's device and entity pages make them:

- ``update_entity``: rename, move to an area, hide, disable (the driver is
  reloaded so it stops or starts publishing it).
- ``set_device_disabled``: disables every entity of the device with
  ``disabled_by: device`` (HA's rule), and enabling restores exactly those.
- ``set_entry_disabled``: unloads or starts the integration entry.
- ``sync_entities``: HA adds entities when an integration finds something new
  (a Music Assistant player, a Roborock vacuum, an Emporia circuit). The hub's
  drivers only drive what the registry holds, so this reads the loaded driver's
  own device list and registers what is missing. It runs after such an entry is
  added and on demand.
- ``update_address``: a device discovery found at a new address (the same
  unique id, a new lease) gets that address on its entry, which reloads.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse, urlunparse

from services.homehub import capabilities, entries
from services.homehub.core import UNAVAILABLE, DeviceEntry, EntityEntry
from services.homehub.discovery import current_host, norm_mac

log = logging.getLogger("homehub.hardware")

#: How HA discovers each integration the hub drives (the protocols ``discovery.py`` listens to).
DISCOVERY_SOURCES: dict[str, list[str]] = {
    "esphome": ["zeroconf"], "matter": ["zeroconf"], "homekit_controller": ["zeroconf"],
    "cast": ["zeroconf"], "androidtv_remote": ["zeroconf"], "music_assistant": ["zeroconf"],
    "tasmota": ["zeroconf", "dhcp"], "reolink": ["zeroconf", "dhcp"], "roborock": ["dhcp"],
    "wiz": ["udp", "dhcp"], "roku": ["ssdp"], "samsungtv": ["ssdp"], "webostv": ["ssdp"], "dlna_dmr": ["ssdp"],
}
#: Integrations that add themselves (an app registers) or are part of the hub.
SELF_ADDED: dict[str, str] = {
    "mobile_app": "Phones add themselves when the Jarvis or Home Assistant companion app signs in.",
    # BLE sensors are listed under New devices once a Bluetooth scanner hears them (services/homehub/bluetooth.py).
    **{d: "Found when a Bluetooth scanner hears it; add it from New devices." for d in (
        "bthome", "xiaomi_ble", "qingping", "sensorpush", "thermopro", "inkbird", "ruuvitag_ble", "govee_ble")},
}
#: Entry states, best to worst, for an integration's overall status.
_STATE_RANK = {"loaded": 0, "not_loaded": 1, "setup_retry": 2, "setup_error": 3}
#: How long a flow waits for a new entry to connect before reading its devices.
SYNC_WAIT_S = 20.0


def _area_names(hub: Any) -> dict[str, str]:
    return {a.area_id: a.name for a in hub.areas.values()}


def _fmt_mac(raw: Any) -> str | None:
    m = norm_mac(raw)
    return ":".join(m[i:i + 2] for i in range(0, 12, 2)) if m else None


def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


def _status(entry_state: str | None, disabled: bool, total: int, unavailable: int) -> str:
    """online / partial / offline / disabled / not_loaded: what the list's dot says."""
    if disabled:
        return "disabled"
    if total == 0:
        return "online" if entry_state in (None, "loaded") else ("offline" if entry_state in (
            "setup_retry", "setup_error") else "not_loaded")
    if unavailable == 0:
        return "online"
    return "offline" if unavailable >= total else "partial"


def _finding_hosts(runtime: Any) -> dict[str, str]:
    """MAC/identifier token -> the address discovery last saw it at."""
    mgr = getattr(runtime, "discovery", None)
    out: dict[str, str] = {}
    if mgr is None:
        return out
    from services.homehub.discovery import token

    for f in mgr.findings.values():
        host = f.get("host")
        if not host:
            continue
        for value in [f.get("mac"), *(f.get("ids") or [])]:
            t = token(value)
            if len(t) >= 6:
                out.setdefault(t, str(host))
    return out


def _connections(dev: DeviceEntry, entry: Any, finding_hosts: dict[str, str], shared: bool) -> list[dict[str, str]]:
    from services.homehub.discovery import token

    out: list[dict[str, str]] = []
    ip = None
    for pair in dev.connections:
        if len(pair) < 2:
            continue
        kind, value = str(pair[0]), str(pair[1])
        if kind == "mac":
            out.append({"type": "mac", "value": _fmt_mac(value) or value})
        elif kind in ("bluetooth", "zigbee", "upnp"):
            out.append({"type": kind, "value": value})
        if ip is None:
            ip = finding_hosts.get(token(value))
    if ip is None:
        for pair in dev.identifiers:
            if len(pair) >= 2 and (found := finding_hosts.get(token(pair[1]))):
                ip = found
                break
    if ip is None and entry is not None and not shared and not dev.via_device_id:
        ip = current_host(entry)
    if ip:
        out.insert(0, {"type": "ip", "value": ip})
    return out


def _entity_counts(hub: Any, device_id: str) -> tuple[int, int, Any]:
    """(enabled entities, unavailable ones, latest update of an available one)."""
    total = unavailable = 0
    last = None
    for eid in hub.device_entities(device_id):
        ent = hub.entities.get(eid)
        if ent is None or ent.disabled_by:
            continue
        total += 1
        st = hub.states.get(eid)
        if st is None or st.state == UNAVAILABLE:
            unavailable += 1
            continue
        if last is None or st.last_updated > last:
            last = st.last_updated
    return total, unavailable, last


def device_view(runtime: Any, dev: DeviceEntry, *, areas: dict[str, str] | None = None,
                finding_hosts: dict[str, str] | None = None) -> dict[str, Any]:
    hub = runtime.hub
    areas = areas if areas is not None else _area_names(hub)
    finding_hosts = finding_hosts if finding_hosts is not None else _finding_hosts(runtime)
    links = [hub.config_entries[e] for e in dev.config_entries if e in hub.config_entries]
    entry = links[0] if links else None
    total, unavailable, last = _entity_counts(hub, dev.id)
    disabled = bool(dev.disabled_by) or bool(entry is not None and entry.disabled_by)
    via = hub.devices.get(dev.via_device_id or "")
    shared = entry is not None and entry.domain in entries.SHARED_ENTRIES
    return {
        "id": dev.id, "name": dev.display_name or (entry.title if entry else dev.id),
        "original_name": dev.name, "name_by_user": dev.name_by_user,
        "manufacturer": dev.manufacturer, "model": dev.model, "model_id": dev.model_id,
        "sw_version": dev.sw_version, "hw_version": dev.hw_version, "serial_number": dev.serial_number,
        "area_id": dev.area_id, "area": areas.get(dev.area_id or ""),
        "domain": entry.domain if entry else None, "label": entries.label(entry.domain) if entry else None,
        "integrations": [{"entry_id": e.entry_id, "domain": e.domain, "label": entries.label(e.domain),
                          "title": e.title, "state": e.state} for e in links],
        "entry_id": entry.entry_id if entry else None, "entry_title": entry.title if entry else None,
        "entry_state": entry.state if entry else None,
        "entry_reason": entry.reason if entry is not None and entry.state != "loaded" else None,
        "connections": _connections(dev, entry, finding_hosts, shared),
        "configuration_url": dev.configuration_url, "entry_type": dev.entry_type,
        "via_device_id": dev.via_device_id, "via_device_name": via.display_name if via else None,
        "disabled_by": dev.disabled_by, "entities": total, "unavailable": unavailable,
        "status": _status(entry.state if entry else None, disabled, total, unavailable),
        "last_seen": _iso(last),
        "abilities": capabilities.device_capabilities(hub, dev.id)["abilities"],
        # Which of maker/model/firmware/serial/MAC the device itself reported, and which only the import holds.
        "info_source": dev.info_source(),
    }


#: A device's kind, from the entities it has, most telling first (the Devices page groups by it).
KIND_ORDER = (("phone", ("device_tracker",)), ("voice", ("assist_satellite",)), ("camera", ("camera",)),
              ("climate", ("climate", "water_heater", "fan", "humidifier")), ("media", ("media_player", "remote")),
              ("light", ("light",)), ("vacuum", ("vacuum", "lawn_mower")), ("access", ("lock", "cover", "valve")),
              ("power", ("switch",)), ("sensor", ("sensor", "binary_sensor", "event")))


def device_kind(hub: Any, device_id: str, domain: str | None) -> str:
    """phone, voice, camera, climate, media, light, vacuum, access, power, sensor or other."""
    if domain == "mobile_app":
        return "phone"
    have = {eid.split(".", 1)[0] for eid in hub.device_entities(device_id)
            if (e := hub.entities.get(eid)) is not None and not e.entity_category}
    for kind, domains in KIND_ORDER:
        if have & set(domains):
            return kind
    return "other"


def is_service(dev: DeviceEntry, domain: str | None) -> bool:
    """Not a device in the house: an add-on, an assistant, a cloud account (HA's entry_type "service")."""
    from services.homehub.drivers import REPLACED

    return dev.entry_type == "service" or (domain or "") in REPLACED or domain in ("marketplace",)


def _real_mac(value: str) -> str | None:
    """A MAC a maker assigned. Locally administered ones (a mesh bridge, a phone's private
    address) are shared by unrelated devices, so they never mean "the same device"."""
    m = norm_mac(value)
    return m if m and not int(m[0:2], 16) & 0x02 else None


def physical_groups(views: list[dict[str, Any]]) -> dict[str, str]:
    """device id -> the id of the device standing for that physical thing.

    Integrations each register what they see: a TV is a Roku device, a Music Assistant player
    and a Cast device. Two views are one thing when they share a real MAC, or when different
    integrations give them the same name and the same model or room. One integration's own
    devices are never merged with each other (several monitors can share a name, a camera
    behind a hub reports the hub's MAC). The standing-for device
    is the one in a room with the most entities, Music Assistant's player last.
    """
    parent = {v["id"]: v["id"] for v in views}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    # Within one integration a shared MAC is a parent and its children (cameras behind a hub
    # report the hub's), so a MAC only joins views from different integrations.
    by_mac: dict[str, list[dict[str, Any]]] = {}
    for v in views:
        for c in v.get("connections") or []:
            if c.get("type") == "mac" and (mac := _real_mac(c.get("value", ""))):
                for other in by_mac.setdefault(mac, []):
                    if other.get("domain") != v.get("domain"):
                        union(v["id"], other["id"])
                by_mac[mac].append(v)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for v in views:
        if v.get("service"):
            continue
        by_name.setdefault(str(v.get("name") or "").strip().lower(), []).append(v)
    for same in by_name.values():
        for i, a in enumerate(same):
            for b in same[i + 1:]:
                if a.get("domain") == b.get("domain"):
                    continue
                model = a.get("model") and str(a.get("model")).lower() == str(b.get("model") or "").lower()
                room = a.get("area_id") and a.get("area_id") == b.get("area_id")
                if model or room:
                    union(a["id"], b["id"])
    members: dict[str, list[dict[str, Any]]] = {}
    for v in views:
        members.setdefault(find(v["id"]), []).append(v)
    out: dict[str, str] = {}
    for group in members.values():
        best = max(group, key=lambda v: (bool(v.get("area_id")), v.get("domain") != "music_assistant",
                                         v.get("entities") or 0, v["id"]))
        for v in group:
            out[v["id"]] = best["id"]
    return out


def device_list(runtime: Any) -> dict[str, Any]:
    hub = runtime.hub
    areas = _area_names(hub)
    hosts = _finding_hosts(runtime)
    devices = [device_view(runtime, d, areas=areas, finding_hosts=hosts) for d in hub.devices.values()
               if not any((e := hub.config_entries.get(x)) is not None and e.source == "ignore"
                          for x in d.config_entries)]
    for d in devices:
        dev = hub.devices[d["id"]]
        d["kind"] = device_kind(hub, d["id"], d["domain"])
        d["service"] = is_service(dev, d["domain"])
    groups = physical_groups(devices)
    for d in devices:
        d["group_id"] = groups[d["id"]]
        d["group_primary"] = groups[d["id"]] == d["id"]
    devices.sort(key=lambda d: str(d["name"]).lower())
    return {"devices": devices,
            "areas": sorted(({"area_id": k, "name": v} for k, v in areas.items()), key=lambda a: a["name"].lower())}


def entity_view(hub: Any, ent: EntityEntry) -> dict[str, Any]:
    from services.homehub.dashboard_api import summarize

    st = summarize(hub, ent.entity_id) if not ent.disabled_by else None
    return {
        "entity_id": ent.entity_id, "domain": ent.domain, "platform": ent.platform,
        "name": hub.friendly_name(ent), "name_by_user": ent.name, "original_name": ent.original_name,
        "entity_category": ent.entity_category, "disabled_by": ent.disabled_by, "hidden_by": ent.hidden_by,
        "area_id": ent.area_id, "icon": ent.icon or ent.original_icon,
        "state": st, "capabilities": capabilities.entity_capabilities(hub, ent.entity_id) if not ent.disabled_by
        else None,
        "info_source": ent.info_source(),
    }


def device_detail(runtime: Any, device_id: str) -> dict[str, Any]:
    hub = runtime.hub
    dev = hub.devices.get(device_id)
    if dev is None:
        raise entries.EntryError("No such device", 404)
    view = device_view(runtime, dev)
    ents = [hub.entities[e] for e in hub.device_entities(dev.id) if e in hub.entities]
    order = {None: 0, "config": 1, "diagnostic": 2}
    ents.sort(key=lambda e: (bool(e.disabled_by), order.get(e.entity_category, 3), e.entity_id))
    children = [{"id": d.id, "name": d.display_name, "model": d.model} for d in hub.devices.values()
                if d.via_device_id == dev.id]
    entry = hub.config_entries.get(view["entry_id"] or "")
    return {**view, "entity_list": [entity_view(hub, e) for e in ents], "children": children,
            "entry": entries.summary(runtime, entry) if entry is not None else None}


# -- integrations --------------------------------------------------------------------------

def integrations(runtime: Any) -> dict[str, Any]:
    from services.homehub import manual_flows
    from services.homehub.drivers import DRIVERS

    hub = runtime.hub
    by_domain: dict[str, list[Any]] = {}
    for e in hub.config_entries.values():
        if e.source != "ignore":
            by_domain.setdefault(e.domain, []).append(e)
    new_found: dict[str, int] = {}
    moved: dict[str, int] = {}
    mgr = getattr(runtime, "discovery", None)
    if mgr is not None:
        for f in mgr.list():
            if f.get("new"):
                new_found[f["integration"]] = new_found.get(f["integration"], 0) + 1
            if f.get("moved"):
                moved[f["integration"]] = moved.get(f["integration"], 0) + 1
    manual = manual_flows.all_manual()
    rows = []
    for domain in sorted(set(by_domain) | set(DRIVERS)):
        ents_ = by_domain.get(domain, [])
        ids = {e.entry_id for e in ents_}
        devices = [d for d in hub.devices.values() if ids & set(d.config_entries)]
        enabled = [x for x in hub.entities.values() if x.config_entry_id in ids and not x.disabled_by]
        unavailable = sum(1 for x in enabled if (s := hub.states.get(x.entity_id)) is None or s.state == UNAVAILABLE)
        live = [e for e in ents_ if not e.disabled_by]
        worst = max((e.state for e in live), key=lambda s: _STATE_RANK.get(s, 1), default=None)
        has_driver = domain in DRIVERS
        status = ("not_set_up" if not ents_ else "disabled" if not live
                  else "unsupported" if not has_driver else worst or "not_loaded")
        rows.append({
            "domain": domain, "label": entries.label(domain), "has_driver": has_driver, "status": status,
            "entries": [{"entry_id": e.entry_id, "title": e.title, "state": e.state, "reason": e.reason,
                         "disabled_by": e.disabled_by, "source": e.source} for e in sorted(ents_, key=lambda e: e.title)],
            "devices": len(devices), "entities": len(enabled), "unavailable": unavailable,
            "discovery": DISCOVERY_SOURCES.get(domain, []),
            "manual": domain in manual, "self_added": SELF_ADDED.get(domain),
            "syncs": domain in SYNCERS, "new_found": new_found.get(domain, 0), "moved": moved.get(domain, 0),
        })
    rank = {"setup_error": 0, "setup_retry": 1, "not_loaded": 2, "loaded": 3, "disabled": 4, "unsupported": 5,
            "not_set_up": 6}
    rows.sort(key=lambda r: (rank.get(r["status"], 3) >= 6, r["label"].lower()))
    return {"integrations": rows}


# -- changes --------------------------------------------------------------------------------

def _entry_of(hub: Any, ent: EntityEntry) -> Any:
    return hub.config_entries.get(ent.config_entry_id or "")


async def _reload(runtime: Any, entry: Any) -> None:
    if entry is None:
        return
    await runtime.stop_entry(entry.entry_id)
    if not entry.disabled_by and entry.source != "ignore":
        runtime.start_entry(entry)


def _clear_state(hub: Any, entity_id: str) -> None:
    hub.states.remove(entity_id)


async def update_entity(runtime: Any, entity_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """``{"name"?: str|null, "area_id"?: str|null, "hidden"?: bool, "disabled"?: bool}``."""
    hub = runtime.hub
    ent = hub.entities.get(entity_id)
    if ent is None:
        raise entries.EntryError("No such entity", 404)
    unknown = sorted(set(body) - {"name", "area_id", "hidden", "disabled"})
    if unknown:
        raise entries.EntryError(f"An entity cannot change {', '.join(unknown)}")
    reload_needed = False
    if "name" in body:
        name = body.get("name")
        ent.name = (str(name).strip() or None) if name is not None else None
    if "area_id" in body:
        area = body.get("area_id")
        if area in (None, ""):
            ent.area_id = None
        else:
            found = hub.area_id_by_name(str(area))
            if found is None:
                raise entries.EntryError(f"There is no area {area!r}")
            ent.area_id = found
    if "hidden" in body:
        ent.hidden_by = "user" if body.get("hidden") else None
    if "disabled" in body:
        want = bool(body.get("disabled"))
        if want and not ent.disabled_by:
            ent.disabled_by = "user"
            _clear_state(hub, entity_id)
            reload_needed = True
        elif not want and ent.disabled_by:
            ent.disabled_by = None
            reload_needed = True
    runtime.store.save_entity(ent)
    hub.bus.fire("entity_registry_updated", {"action": "update", "entity_id": entity_id})
    st = hub.states.get(entity_id)
    if st is not None and ("name" in body):
        attrs = dict(st.attributes)
        attrs["friendly_name"] = hub.friendly_name(ent)
        hub.states.set(entity_id, st.state, attrs)
    if reload_needed:
        await _reload(runtime, _entry_of(hub, ent))
    return entity_view(hub, ent)


async def set_device_disabled(runtime: Any, device_id: str, disabled: bool) -> dict[str, Any]:
    """Disable a device and (HA's rule) its enabled entities with ``disabled_by: device``; enabling undoes that."""
    hub = runtime.hub
    dev = hub.devices.get(device_id)
    if dev is None:
        raise entries.EntryError("No such device", 404)
    changed = []
    for eid in hub.device_entities(dev.id):
        ent = hub.entities[eid]
        if disabled and not ent.disabled_by:
            ent.disabled_by = "device"
            _clear_state(hub, eid)
        elif not disabled and ent.disabled_by == "device":
            ent.disabled_by = None
        else:
            continue
        runtime.store.save_entity(ent)
        changed.append(eid)
    dev.disabled_by = "user" if disabled else None
    runtime.store.save_device(dev)
    hub.bus.fire("device_registry_updated", {"action": "update", "device_id": dev.id})
    for entry_id in dev.config_entries:
        await _reload(runtime, hub.config_entries.get(entry_id))
    log.info("%s device %s (%d entities)", "disabled" if disabled else "enabled", dev.display_name, len(changed))
    return {**device_view(runtime, dev), "changed_entities": changed}


async def set_entry_disabled(runtime: Any, entry_id: str, disabled: bool) -> dict[str, Any]:
    hub = runtime.hub
    entry = hub.config_entries.get(entry_id)
    if entry is None:
        raise entries.EntryError("No such integration entry", 404)
    entry.disabled_by = "user" if disabled else None
    try:
        runtime.store.save_config_entry(entry)
    except RuntimeError as exc:
        raise entries.EntryError(str(exc), 503) from exc
    await runtime.stop_entry(entry_id)
    if disabled:
        entry.state, entry.reason = "not_loaded", "Disabled by an admin"
        for ent in hub.entities.values():
            if ent.config_entry_id == entry_id and not ent.disabled_by:
                st = hub.states.get(ent.entity_id)
                hub.publish(ent, UNAVAILABLE, dict(st.attributes) if st else {})
    else:
        entry.state, entry.reason = "not_loaded", None
        runtime.start_entry(entry)
    log.info("%s %s %s", "disabled" if disabled else "enabled", entry.domain, entry.title)
    return entries.summary(runtime, entry)


async def update_address(runtime: Any, finding_id: str) -> dict[str, Any]:
    """Give the entry of a device found at a new address that address (discovery's ``moved``)."""
    mgr = getattr(runtime, "discovery", None)
    if mgr is None:
        raise entries.EntryError("Device discovery is not running", 503)
    finding = mgr.get(finding_id)
    if finding is None:
        raise entries.EntryError("No such discovered device", 404)
    moved = mgr.public(finding).get("moved")
    if not moved:
        raise entries.EntryError("This device's address has not changed", 409)
    entry = runtime.hub.config_entries[moved["entry_id"]]
    new_host = str(moved["new_host"])
    data = entry.data or {}
    if "host" in data:
        change = {"host": new_host}
    elif "url" in data:
        u = urlparse(str(data["url"]))
        netloc = new_host + (f":{u.port}" if u.port else "")
        if u.username:
            netloc = f"{u.username}{':' + u.password if u.password else ''}@{netloc}"
        change = {"url": urlunparse(u._replace(netloc=netloc))}
    else:
        raise entries.EntryError(f"{entry.title} does not keep an address the hub can change", 409)
    summary = await entries.reconfigure_entry(runtime, entry.entry_id, {"data": change})
    log.info("%s %s moved %s -> %s", entry.domain, entry.title, moved["old_host"], new_host)
    return {"status": "updated", "entry": summary, "old_host": moved["old_host"], "new_host": new_host}


# -- entity sync (new sub-devices of a loaded integration) ---------------------------------

def _plan_for(runtime: Any, entry: Any) -> Any:
    from services.homehub.flows import Plan

    return Plan(runtime.hub, entry, new_entry=False)


def _has_unique(hub: Any, entry_id: str, domain: str, unique_id: str) -> bool:
    want = str(unique_id).lower()
    return any(e.config_entry_id == entry_id and e.domain == domain and str(e.unique_id).lower() == want
               for e in hub.entities.values())


def _sync_music_assistant(runtime: Any, entry: Any, driver: Any) -> Any:
    plan = _plan_for(runtime, entry)
    for pid, p in sorted((getattr(driver, "players", None) or {}).items()):
        if _has_unique(runtime.hub, entry.entry_id, "media_player", pid):
            continue
        info = p.get("device_info") if isinstance(p.get("device_info"), dict) else {}
        name = str(p.get("display_name") or p.get("name") or pid)
        dev = plan.add_device(name, manufacturer=info.get("manufacturer") or None, model=info.get("model") or None,
                              identifiers=[["music_assistant", pid]])
        plan.add_entity("media_player", pid, None, device=dev)
    return plan


def _sync_roborock(runtime: Any, entry: Any, driver: Any) -> Any:
    plan = _plan_for(runtime, entry)
    for duid, dev_obj in sorted((getattr(driver, "devices", None) or {}).items()):
        if _has_unique(runtime.hub, entry.entry_id, "vacuum", duid):
            continue
        product = getattr(dev_obj, "product", None)
        name = str(getattr(dev_obj, "name", None) or getattr(product, "name", None) or duid)
        dev = plan.add_device(name, manufacturer="Roborock", model=getattr(product, "model", None),
                              identifiers=[["roborock", str(duid)]])
        plan.add_entity("vacuum", str(duid).lower(), None, device=dev)
    return plan


_EMPORIA_SCALES = (("instant", "Power", {"original_device_class": "power", "unit_of_measurement": "W",
                                          "capabilities": {"state_class": "measurement"}}),
                   ("1D", "Energy today", {"original_device_class": "energy", "unit_of_measurement": "kWh",
                                           "capabilities": {"state_class": "total"}}),
                   ("1MON", "Energy this month", {"original_device_class": "energy", "unit_of_measurement": "kWh",
                                                  "capabilities": {"state_class": "total"}}))


def _sync_emporia(runtime: Any, entry: Any, driver: Any) -> Any:
    hub = runtime.hub
    plan = _plan_for(runtime, entry)
    for gid, dev_obj in sorted((getattr(driver, "devices", None) or {}).items()):
        device = next((d for d in hub.devices.values() if entry.entry_id in d.config_entries
                       and ["emporia_vue", str(gid)] in d.identifiers), None)
        for ch in getattr(dev_obj, "channels", None) or []:
            channel = str(getattr(ch, "channel_num", ""))
            if not channel:
                continue
            ch_name = getattr(ch, "name", None) or (getattr(dev_obj, "device_name", None) if channel == "1,2,3"
                                                     else f"Circuit {channel}")
            for scale, label, fields in _EMPORIA_SCALES:
                uid = f"sensor.emporia_vue.{scale}.{gid}-{channel}"
                if _has_unique(hub, entry.entry_id, "sensor", uid):
                    continue
                if device is None:
                    device = plan.add_device(str(getattr(dev_obj, "device_name", None) or f"Emporia {gid}"),
                                             manufacturer="Emporia", model=getattr(dev_obj, "model", None),
                                             sw_version=getattr(dev_obj, "firmware", None),
                                             identifiers=[["emporia_vue", str(gid)]])
                plan.add_entity("sensor", uid, f"{ch_name} {label}", device=device, **fields)
    return plan


SYNCERS = {"music_assistant": _sync_music_assistant, "roborock": _sync_roborock, "emporia_vue": _sync_emporia}


async def sync_entities(runtime: Any, entry_id: str) -> dict[str, Any]:
    """Register what a loaded driver knows about and the registry does not; restarts it to pick them up."""
    from services.homehub.flows import commit

    hub = runtime.hub
    entry = hub.config_entries.get(entry_id)
    if entry is None:
        raise entries.EntryError("No such integration entry", 404)
    syncer = SYNCERS.get(entry.domain)
    if syncer is None:
        raise entries.EntryError(f"{entries.label(entry.domain)} adds its devices when they are set up; "
                                 "there is nothing to sync", 400)
    driver = hub.drivers.get(entry_id)
    if driver is None or entry.state != "loaded":
        raise entries.EntryError(f"{entry.title} is not connected ({entry.reason or entry.state}); "
                                 "its devices can be read once it connects", 409)
    plan = syncer(runtime, entry, driver)
    if not plan.entities and not plan.devices:
        return {"status": "synced", "entry_id": entry_id, "devices": [], "entities": []}
    result = await commit(runtime, plan, restart=True)
    return {"status": "synced", "entry_id": entry_id, "devices": [d.id for d in plan.devices],
            "entities": result["entities"]}


async def sync_after_load(runtime: Any, entry_id: str, wait_s: float | None = None) -> dict[str, Any]:
    """After a flow added an entry that finds its own devices: wait for it to connect, then sync."""
    hub = runtime.hub
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (SYNC_WAIT_S if wait_s is None else wait_s)
    while loop.time() < deadline:
        entry = hub.config_entries.get(entry_id)
        if entry is None:
            break
        if entry.state == "loaded" and entry_id in hub.drivers:
            try:
                return await sync_entities(runtime, entry_id)
            except entries.EntryError as exc:
                return {"status": "not_synced", "message": str(exc)}
        if entry.state in ("setup_retry", "setup_error") and entry.reason:
            return {"status": "not_synced", "message": f"It is set up but did not connect: {entry.reason}"}
        await asyncio.sleep(0.5)
    return {"status": "not_synced",
            "message": "It is set up and still connecting; its devices appear after Sync devices on Integrations."}
