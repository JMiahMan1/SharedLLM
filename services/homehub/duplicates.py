"""Hiding duplicate devices: the same physical thing registered by two integrations.

A bulb on the network is often in the hub twice: as a Matter device (paired through the fabric) and as a WiZ
device (found on the LAN). Both work, and deleting the Matter one would decommission it from the fabric, so a
person hides one integration's copy instead.

**Hidden is not disabled.** A hidden device keeps working: its states update, automations and schedules that
name it (or its entities) still run, and services still accept it. It is only left out of what people and
Jarvis browse: the rooms dashboard, Jarvis's entity lists and pickers, the fast-path entity cache, the RAG
entity sync, the capability index's room lookups, the voice exposure list, and a service call aimed at a whole
area. Hidden lives on the device (``DeviceEntry.hidden_by = "user"``), survives restarts, resyncs and a
re-import, and its entities follow it with ``hidden_by: "device"`` (Home Assistant's rule for disabling).
Unhiding restores everything.

**Duplicates** are devices of different integrations that are the same thing: the same real MAC when both
report one, otherwise the same name (a person's name for it, else the integration's) in the same area. Two
devices of one integration are never grouped (a house can have six bulbs that share a name in different
rooms, or a hub and its children that share a MAC).

| Route (``entries_api.py``) | What |
|---|---|
| ``PATCH /hub/devices/{id}`` ``{"hidden": bool}`` | Hide or show one device |
| ``GET /hub/devices/duplicates`` | ``{"groups": [...], "integrations": [...]}`` |
| ``POST /hub/devices/duplicates/hide`` | ``{"hide_domain"?, "keep_domain"?}``: hide one integration's copies |
| ``POST /hub/devices/duplicates/unhide`` | ``{"domain"}``: show that integration's copies again |
"""
from __future__ import annotations

import re
from typing import Any

from services.homehub import entries
from services.homehub.core import DeviceEntry, Hub
from services.homehub.discovery import norm_mac

HIDDEN_BY_USER = "user"
HIDDEN_BY_DEVICE = "device"


def _norm_name(text: str | None) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", str(text or "").lower()).split())


def _real_macs(dev: DeviceEntry) -> set[str]:
    """MACs a maker assigned. Locally administered ones are shared by unrelated devices and never count."""
    out = set()
    for pair in dev.connections or []:
        if len(pair) == 2 and str(pair[0]).lower() == "mac":
            m = norm_mac(str(pair[1]))
            if m and not int(m[0:2], 16) & 0x02:
                out.add(m)
    return out


def _domain(hub: Hub, dev: DeviceEntry) -> str | None:
    for entry_id in dev.config_entries:
        entry = hub.config_entries.get(entry_id)
        if entry is not None:
            return None if entry.source == "ignore" else entry.domain
    return None


def _candidates(hub: Hub) -> list[tuple[DeviceEntry, str]]:
    from services.homehub.hardware import is_service

    out = []
    for dev in hub.devices.values():
        domain = _domain(hub, dev)
        if domain and not is_service(dev, domain):
            out.append((dev, domain))
    return sorted(out, key=lambda x: x[0].id)


def duplicate_groups(hub: Hub) -> list[dict[str, Any]]:
    """Groups of two or more devices of different integrations that are one physical thing (raw, unsorted)."""
    cands = _candidates(hub)
    domain_of = {dev.id: domain for dev, domain in cands}
    parent = {dev.id: dev.id for dev, _ in cands}
    domains = {dev.id: {domain} for dev, domain in cands}
    how: dict[str, str] = {}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    macs = {dev.id: _real_macs(dev) for dev, _ in cands}
    group_macs = {dev.id: set(macs[dev.id]) for dev, _ in cands}

    def union(a: str, b: str, match: str) -> None:
        ra, rb = find(a), find(b)
        # Never two devices of one integration in a group, nor two different MACs, even through a third.
        if ra == rb or domains[ra] & domains[rb]:
            return
        if group_macs[ra] and group_macs[rb] and not group_macs[ra] & group_macs[rb]:
            return
        parent[rb] = ra
        domains[ra] |= domains.pop(rb)
        group_macs[ra] |= group_macs.pop(rb)
        how[ra] = "mac" if "mac" in (match, how.get(ra), how.get(rb)) else match

    by_mac: dict[str, list[str]] = {}
    for dev, _ in cands:
        for m in macs[dev.id]:
            by_mac.setdefault(m, []).append(dev.id)
    for ids in by_mac.values():
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if domain_of[a] != domain_of[b]:
                    union(a, b, "mac")
    by_name: dict[tuple[str, str], list[str]] = {}
    for dev, _ in cands:
        name = _norm_name(dev.display_name)
        if name and dev.area_id:
            by_name.setdefault((name, dev.area_id), []).append(dev.id)
    for ids in by_name.values():
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                # Two devices that both report a MAC are the same thing only when it is the same MAC (union).
                if domain_of[a] != domain_of[b]:
                    union(a, b, "name")
    members: dict[str, list[str]] = {}
    for dev, _ in cands:
        members.setdefault(find(dev.id), []).append(dev.id)
    return [{"root": root, "match": how.get(root, "name"), "ids": ids}
            for root, ids in members.items() if len(ids) > 1]


def _last_updated(hub: Hub, entity_ids: list[str]) -> str | None:
    last = None
    for eid in entity_ids:
        st = hub.states.get(eid)
        if st is not None and (last is None or st.last_updated > last):
            last = st.last_updated
    return last.isoformat() if last is not None else None


def _device_row(hub: Hub, dev: DeviceEntry) -> dict[str, Any]:
    entry = next((hub.config_entries[e] for e in dev.config_entries if e in hub.config_entries), None)
    domain = entry.domain if entry else None
    entity_ids = sorted(hub.device_entities(dev.id))
    area = hub.areas.get(dev.area_id or "")
    return {
        "id": dev.id, "name": dev.display_name or dev.id, "manufacturer": dev.manufacturer, "model": dev.model,
        "domain": domain, "label": entries.label(domain) if domain else None,
        "entry_id": entry.entry_id if entry else None, "entry_title": entry.title if entry else None,
        "area_id": dev.area_id, "area": area.name if area else None,
        "entity_ids": entity_ids, "hidden": bool(dev.hidden_by), "hidden_by": dev.hidden_by,
        "last_updated": _last_updated(hub, entity_ids),
    }


def list_duplicates(runtime: Any) -> dict[str, Any]:
    """``{"groups": [{"id", "match", "name", "area", "domains", "devices": [...]}], "integrations": [...]}``.

    Each device row: ``id, name, domain, label, entry_id, entry_title, area_id, area, entity_ids, hidden,
    hidden_by, last_updated``. ``integrations`` lists every integration with copies in a group: how many
    groups it is in, and how many of its copies are hidden.
    """
    hub = runtime.hub
    groups = []
    for g in duplicate_groups(hub):
        rows = sorted((_device_row(hub, hub.devices[i]) for i in g["ids"]),
                      key=lambda r: (str(r["label"] or "").lower(), r["id"]))
        first = min(rows, key=lambda r: r["id"])
        groups.append({"id": first["id"], "match": g["match"], "name": first["name"], "area": first["area"],
                       "domains": sorted({str(r["domain"]) for r in rows}), "devices": rows})
    groups.sort(key=lambda g: (str(g["area"] or "").lower(), str(g["name"]).lower(), g["id"]))
    integrations: dict[str, dict[str, Any]] = {}
    for g in groups:
        for r in g["devices"]:
            row = integrations.setdefault(r["domain"], {"domain": r["domain"], "label": r["label"], "groups": 0,
                                                        "devices": 0, "hidden": 0})
            row["devices"] += 1
            row["hidden"] += 1 if r["hidden"] else 0
        for d in g["domains"]:
            integrations[d]["groups"] += 1
    return {"groups": groups,
            "integrations": sorted(integrations.values(), key=lambda r: str(r["label"] or "").lower())}


def set_device_hidden(runtime: Any, device_id: str, hidden: bool) -> dict[str, Any]:
    """Hide (``hidden_by: user``) or show one device; its entities follow with ``hidden_by: device``.

    An entity a person hid on its own (``hidden_by: user``) stays hidden when the device is shown again.
    """
    hub = runtime.hub
    dev = hub.devices.get(device_id)
    if dev is None:
        raise entries.EntryError("No such device", 404)
    if not isinstance(hidden, bool):
        raise entries.EntryError("hidden must be true or false")
    want = HIDDEN_BY_USER if hidden else None
    if dev.hidden_by != want:
        dev.hidden_by = want
        runtime.store.save_device(dev)
        hub.bus.fire("device_registry_updated", {"action": "update", "device_id": dev.id})
    for eid in hub.device_entities(dev.id):
        ent = hub.entities[eid]
        if hidden and not ent.hidden_by:
            ent.hidden_by = HIDDEN_BY_DEVICE
        elif not hidden and ent.hidden_by == HIDDEN_BY_DEVICE:
            ent.hidden_by = None
        else:
            continue
        runtime.store.save_entity(ent)
        hub.bus.fire("entity_registry_updated", {"action": "update", "entity_id": eid})
    return {"id": dev.id, "name": dev.display_name, "hidden": bool(dev.hidden_by), "hidden_by": dev.hidden_by}


def _domain_arg(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise entries.EntryError(f"{key} must be an integration's domain")
    return value.strip()


def hide_copies(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Hide one integration's copy in every duplicate group.

    ``{"hide_domain": "matter"}`` hides the Matter copy in every group that also has another integration's;
    ``{"keep_domain": "wiz"}`` hides every other integration's copy in each group that has a WiZ one; both
    together hide the Matter copies only in groups that have a WiZ one too. Answers ``{"changed": [ids],
    **list_duplicates}``.
    """
    hide, keep = _domain_arg(body, "hide_domain"), _domain_arg(body, "keep_domain")
    if not hide and not keep:
        raise entries.EntryError("Say which integration's copies to hide (hide_domain) or keep (keep_domain)")
    if hide and hide == keep:
        raise entries.EntryError("hide_domain and keep_domain must be different integrations")
    hub = runtime.hub
    targets: list[str] = []
    for g in list_duplicates(runtime)["groups"]:
        domains = set(g["domains"])
        if keep and keep not in domains:
            continue
        if hide and hide not in domains:
            continue
        for r in g["devices"]:
            if (r["domain"] == hide) if hide else (r["domain"] != keep):
                targets.append(r["id"])
    changed = [i for i in targets if not hub.devices[i].hidden_by]
    for device_id in changed:
        set_device_hidden(runtime, device_id, True)
    return {"changed": changed, **list_duplicates(runtime)}


def show_copies(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    """``{"domain": "matter"}``: show again every hidden copy of that integration in a duplicate group."""
    domain = _domain_arg(body, "domain") or _domain_arg(body, "hide_domain")
    if not domain:
        raise entries.EntryError("Say which integration's copies to show (domain)")
    hub = runtime.hub
    changed = [r["id"] for g in list_duplicates(runtime)["groups"] for r in g["devices"]
               if r["domain"] == domain and hub.devices[r["id"]].hidden_by]
    for device_id in changed:
        set_device_hidden(runtime, device_id, False)
    return {"changed": changed, **list_duplicates(runtime)}
