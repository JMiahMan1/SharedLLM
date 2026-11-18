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
| ``POST /hub/devices/duplicates/combine`` | ``{"main", "fallback"}`` device ids, or ``{"group", "main_domain"?}``, or
  ``{"main_domain", "fallback_domain"}`` = every pair of those two integrations, saved as the household rule |
| ``POST /hub/devices/duplicates/uncombine`` | ``{"device"}`` (either copy): both copies back as they were |
| ``POST /hub/devices/duplicates/swap`` | ``{"device"}`` (either copy): the fallback becomes the main |
| ``POST /hub/devices/duplicates/rule`` | ``{"main_domain", "fallback_domain"}`` sets the household rule for that pair
  of integrations; ``{"clear": [a, b]}`` removes it |

**Pairing needs hard evidence.** The same real MAC (or serial number) on both copies, or the same name in the
same area. A WiZ bulb reports its MAC itself; a Matter node reports its Wi-Fi MAC in General Diagnostics'
NetworkInterfaces, which the Matter driver confirms onto its device (``drivers/matter.py`` ``node_macs``), so a
bulb paired to the fabric and seen on the LAN pairs on its MAC whatever either copy is called. An IP address is
never evidence (DHCP hands it to another device).

**Combined is one device with a fallback.** A person picks which copy is the main (or the household rule for that
pair of integrations does). The main stays shown with its own entity ids; the other copy is hidden
(``hidden_by: "combined"``) and kept as the fallback. Each controllable entity (a light, switch, fan, cover...)
of the main is paired with the fallback's entity of the same kind, and the hub then treats the two ids as one
(``Hub.combined``): both show the main's state while it is available, else the fallback's, so the combined
entity is unavailable only when both copies are; a command to either id goes to the main and, when the main
fails or is known to be unavailable, the same command goes to the fallback (logged at INFO, "used fallback").
This is in the hub's service dispatch, so voice, the app, schedules, automations, light groups and the
HA-compatible REST and websocket APIs all get it. A name or room only one copy has is carried over to the
main (so nothing loses its name or room), and a bare "Light" name gives way to the device's name; everything
changed is recorded, and uncombining restores both copies as they were (a field a person changed since is left
alone).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from services.homehub import entries
from services.homehub.core import UNAVAILABLE, DeviceEntry, Hub, utcnow
from services.homehub.discovery import norm_mac

log = logging.getLogger("homehub.duplicates")

HIDDEN_BY_USER = "user"
HIDDEN_BY_DEVICE = "device"
#: The fallback copy of a combined device.
HIDDEN_BY_COMBINED = "combined"
#: Store keys (``kv``): the combinations, and the household's main/fallback rule per pair of integrations.
COMBINED_KEY = "combined_devices"
RULES_KEY = "duplicate_rules"
#: Entity domains a combination pairs: what a person controls. A sensor or a diagnostic is the copy's own.
PAIRED_DOMAINS = ("light", "switch", "fan", "cover", "lock", "climate", "media_player", "valve", "humidifier",
                  "vacuum", "water_heater", "siren")
#: Entity names that say what a thing is but not which one ("Light"): the device's name is better.
GENERIC_NAMES = {"light", "lamp", "bulb", "switch", "plug", "outlet", "socket", "fan", "cover", "lock",
                 "media player", "speaker", "thermostat", "valve", "siren"}


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


def _serials(dev: DeviceEntry) -> set[str]:
    """A serial number the maker printed, as a hard identity (normalised; short placeholders never count)."""
    serial = re.sub(r"[^0-9a-z]", "", str(dev.serial_number or "").lower())
    return {serial} if len(serial) >= 6 and len(set(serial)) > 2 else set()


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

    # Hard identities: a real MAC, a serial number. Kept per kind, so a copy that reports only a serial can still
    # join one that reports only a MAC by name, while two different MACs (or serials) are two things.
    ids = {dev.id: {"mac": _real_macs(dev), "serial": _serials(dev)} for dev, _ in cands}
    group_ids = {dev.id: {k: set(v) for k, v in ids[dev.id].items()} for dev, _ in cands}

    def union(a: str, b: str, match: str) -> None:
        ra, rb = find(a), find(b)
        # Never two devices of one integration in a group, nor two different MACs or serials, even through a third.
        if ra == rb or domains[ra] & domains[rb]:
            return
        for kind in ("mac", "serial"):
            if group_ids[ra][kind] and group_ids[rb][kind] and not group_ids[ra][kind] & group_ids[rb][kind]:
                return
        parent[rb] = ra
        domains[ra] |= domains.pop(rb)
        for kind, values in group_ids.pop(rb).items():
            group_ids[ra][kind] |= values
        rank = ("mac", "serial", "name")
        how[ra] = min((m for m in (match, how.get(ra), how.get(rb)) if m), key=rank.index)

    for kind in ("mac", "serial"):
        by_id: dict[str, list[str]] = {}
        for dev, _ in cands:
            for value in ids[dev.id][kind]:
                by_id.setdefault(value, []).append(dev.id)
        for same in by_id.values():
            for i, a in enumerate(same):
                for b in same[i + 1:]:
                    if domain_of[a] != domain_of[b]:
                        union(a, b, kind)
    by_name: dict[tuple[str, str], list[str]] = {}
    for dev, _ in cands:
        name = _norm_name(dev.display_name)
        if name and dev.area_id:
            by_name.setdefault((name, dev.area_id), []).append(dev.id)
    for named in by_name.values():
        for i, a in enumerate(named):
            for b in named[i + 1:]:
                # Two devices that both report a MAC are the same thing only when it is the same MAC (union).
                if domain_of[a] != domain_of[b]:
                    union(a, b, "name")
    members: dict[str, list[str]] = {}
    for dev, _ in cands:
        members.setdefault(find(dev.id), []).append(dev.id)
    return [{"root": root, "match": how.get(root, "name"), "ids": group}
            for root, group in members.items() if len(group) > 1]


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


# -- combinations: the records ----------------------------------------------------------------------

def _records(runtime: Any) -> list[dict[str, Any]]:
    raw = runtime.store.get_kv(COMBINED_KEY, []) if runtime.store is not None else []
    return [r for r in raw or [] if isinstance(r, dict) and r.get("main") and r.get("fallback")]


def _save_records(runtime: Any, records: list[dict[str, Any]]) -> None:
    runtime.store.set_kv(COMBINED_KEY, records)


def _rules(runtime: Any) -> dict[str, str]:
    """``"a|b"`` (the two domains, sorted) -> the domain that is the main."""
    raw = runtime.store.get_kv(RULES_KEY, {}) if runtime.store is not None else {}
    return {k: v for k, v in (raw or {}).items() if isinstance(k, str) and isinstance(v, str)}


def _rule_key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def record_of(runtime: Any, device_id: str) -> dict[str, Any] | None:
    """The combination a device is in (as its main or its fallback), if any."""
    return next((r for r in _records(runtime) if device_id in (r["main"], r["fallback"])), None)


def apply_combinations(hub: Hub, records: list[dict[str, Any]]) -> None:
    """Point the hub at the saved combinations (at start, and after any change). Missing entities are skipped."""
    hub.combined.clear()
    for r in records:
        for main, fallback in r.get("pairs") or []:
            if main in hub.entities and fallback in hub.entities:
                hub.combined[main] = hub.combined[fallback] = (main, fallback)
    for eid in list(hub.copy_states):
        if eid not in hub.combined:
            hub.copy_states.pop(eid, None)
    hub.fallback_active &= set(hub.combined)


def load_combinations(runtime: Any) -> None:
    """At start: the saved combinations, before any driver publishes. A record whose device is gone is dropped."""
    hub = runtime.hub
    records = _records(runtime)
    keep = [r for r in records if r["main"] in hub.devices and r["fallback"] in hub.devices]
    for r in records:
        if r not in keep:
            log.warning("dropping the combination of %s and %s: a device is gone", r["main"], r["fallback"])
            gone_fallback = hub.devices.get(r["fallback"])
            if gone_fallback is not None and gone_fallback.hidden_by == HIDDEN_BY_COMBINED:
                _set_hidden(runtime, gone_fallback, None)
    if keep != records:
        _save_records(runtime, keep)
    apply_combinations(hub, keep)


# -- combinations: what pairs -----------------------------------------------------------------------

def _controllable(hub: Hub, device_id: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for eid in sorted(hub.device_entities(device_id)):
        ent = hub.entities[eid]
        if ent.domain in PAIRED_DOMAINS and not ent.disabled_by and not ent.entity_category:
            out.setdefault(ent.domain, []).append(eid)
    return out


def entity_pairs(hub: Hub, main_id: str, fallback_id: str) -> list[list[str]]:
    """``[[main entity, fallback entity], ...]``: per controllable domain, when each copy has exactly one."""
    mine, theirs = _controllable(hub, main_id), _controllable(hub, fallback_id)
    return [[mine[d][0], theirs[d][0]] for d in PAIRED_DOMAINS
            if len(mine.get(d, [])) == 1 and len(theirs.get(d, [])) == 1]


def _generic(name: str | None, domain: str) -> bool:
    n = _norm_name(name)
    return not n or n in GENERIC_NAMES or n == domain.replace("_", " ")


def _set_hidden(runtime: Any, dev: DeviceEntry, value: str | None,
                entity_values: dict[str, str | None] | None = None) -> None:
    """Set a device's ``hidden_by``; its entities follow (``device``), or take ``entity_values`` when given."""
    hub = runtime.hub
    if dev.hidden_by != value:
        dev.hidden_by = value
        runtime.store.save_device(dev)
        hub.bus.fire("device_registry_updated", {"action": "update", "device_id": dev.id})
    for eid in hub.device_entities(dev.id):
        ent = hub.entities[eid]
        if entity_values is not None and eid in entity_values:
            want = entity_values[eid]
            # A person who hid the entity on its own since keeps it hidden.
            if ent.hidden_by == HIDDEN_BY_USER and want != HIDDEN_BY_USER:
                continue
        elif value and not ent.hidden_by:
            want = HIDDEN_BY_DEVICE
        elif not value and ent.hidden_by == HIDDEN_BY_DEVICE:
            want = None
        else:
            continue
        if ent.hidden_by == want:
            continue
        ent.hidden_by = want
        runtime.store.save_entity(ent)
        hub.bus.fire("entity_registry_updated", {"action": "update", "entity_id": eid})


def _republish(hub: Hub, entity_ids: list[str]) -> None:
    """Names come from the registry: write each state again with its friendly name."""
    for eid in entity_ids:
        st, ent = hub.states.get(eid), hub.entities.get(eid)
        if st is None or ent is None:
            continue
        if eid in hub.combined:
            hub.publish_combined(eid)
            continue
        attrs = dict(st.attributes)
        attrs["friendly_name"] = hub.friendly_name(ent)
        hub.states.set(eid, st.state, attrs)


# -- combinations: listing -------------------------------------------------------------------------------

def _copy_available(hub: Hub, entity_ids: list[str]) -> bool | None:
    """Whether a copy's own paired entities answer (its own state, not the shared one)."""
    states = [hub.copy_states.get(e) or hub.states.get(e) for e in entity_ids]
    known = [s for s in states if s is not None]
    if not known:
        return None
    return any(s.state != UNAVAILABLE for s in known)


def _combined_view(runtime: Any, r: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    pairs = [p for p in r.get("pairs") or [] if p[0] in hub.entities and p[1] in hub.entities]
    main_dev, fb_dev = hub.devices.get(r["main"]), hub.devices.get(r["fallback"])
    main_domain = _domain(hub, main_dev) if main_dev else None
    fb_domain = _domain(hub, fb_dev) if fb_dev else None
    main_ok = _copy_available(hub, [p[0] for p in pairs])
    fb_ok = _copy_available(hub, [p[1] for p in pairs])
    using = "main" if main_ok else "fallback" if fb_ok else None
    return {
        "main": r["main"], "fallback": r["fallback"],
        "main_domain": main_domain, "main_label": entries.label(main_domain) if main_domain else None,
        "fallback_domain": fb_domain, "fallback_label": entries.label(fb_domain) if fb_domain else None,
        "entities": [{"main": m, "fallback": f} for m, f in pairs],
        "main_available": main_ok, "fallback_available": fb_ok, "using": using,
    }


def list_duplicates(runtime: Any) -> dict[str, Any]:
    """``{"groups": [...], "integrations": [...], "rules": [...]}``.

    Each group: ``id, match ("mac" | "serial" | "name"), name, area, domains, devices`` plus ``combined`` (the
    combination's view, or ``None``), ``can_combine`` (two copies with something to pair), ``suggested_main``
    (the household rule's main for these two integrations, or ``None``). Each device row: ``id, name, domain,
    label, entry_id, entry_title, area_id, area, entity_ids, hidden, hidden_by, last_updated`` plus ``role``
    (``"main"``, ``"fallback"`` or ``None``). ``integrations`` lists every integration with copies in a group: how
    many groups it is in, and how many of its copies are hidden (combined groups are left out: the combination decides
which copy shows). ``rules`` is the household's main/fallback rule
    per pair of integrations.
    """
    hub = runtime.hub
    records = _records(runtime)
    by_device = {d: r for r in records for d in (r["main"], r["fallback"])}
    rules = _rules(runtime)
    raw = duplicate_groups(hub)
    grouped = {i for g in raw for i in g["ids"]}
    for r in records:  # a combination whose copies no longer look alike (renamed) is still listed
        if r["main"] not in grouped and r["main"] in hub.devices and r["fallback"] in hub.devices:
            raw.append({"root": r["main"], "match": "combined", "ids": [r["main"], r["fallback"]]})
    groups = []
    for g in raw:
        rows = sorted((_device_row(hub, hub.devices[i]) for i in g["ids"]),
                      key=lambda r: (str(r["label"] or "").lower(), r["id"]))
        rec = next((by_device[r["id"]] for r in rows if r["id"] in by_device), None)
        for row in rows:
            row["role"] = ("main" if rec["main"] == row["id"] else "fallback" if rec["fallback"] == row["id"]
                           else None) if rec else None
        main_row = next((r for r in rows if r["role"] == "main"), None)
        first = main_row or min(rows, key=lambda r: r["id"])
        domains = sorted({str(r["domain"]) for r in rows})
        can = (rec is None and len(rows) == 2 and len(domains) == 2
               and bool(entity_pairs(hub, rows[0]["id"], rows[1]["id"])))
        groups.append({"id": first["id"], "match": g["match"], "name": first["name"], "area": first["area"],
                       "domains": domains, "devices": rows,
                       "combined": _combined_view(runtime, rec) if rec else None, "can_combine": can,
                       "suggested_main": rules.get(_rule_key(*domains)) if len(domains) == 2 else None})
    groups.sort(key=lambda g: (str(g["area"] or "").lower(), str(g["name"]).lower(), g["id"]))
    integrations: dict[str, dict[str, Any]] = {}
    for g in groups:
        if g["combined"]:  # the combination decides which copy shows; hiding is for the others
            continue
        for r in g["devices"]:
            row = integrations.setdefault(r["domain"], {"domain": r["domain"], "label": r["label"], "groups": 0,
                                                        "devices": 0, "hidden": 0})
            row["devices"] += 1
            row["hidden"] += 1 if r["hidden"] else 0
        for d in g["domains"]:
            integrations[d]["groups"] += 1
    rule_rows = []
    for key, main in sorted(rules.items()):
        a, b = key.split("|", 1)
        fallback = b if main == a else a
        rule_rows.append({"domains": [a, b], "main_domain": main, "main_label": entries.label(main),
                          "fallback_domain": fallback, "fallback_label": entries.label(fallback)})
    return {"groups": groups,
            "integrations": sorted(integrations.values(), key=lambda r: str(r["label"] or "").lower()),
            "rules": rule_rows}


# -- hiding ---------------------------------------------------------------------------------------------

def _refuse_if_combined(runtime: Any, device_id: str) -> None:
    if record_of(runtime, device_id) is not None:
        raise entries.EntryError("This device is combined with its other copy. Uncombine it first.", 409)


def set_device_hidden(runtime: Any, device_id: str, hidden: bool) -> dict[str, Any]:
    """Hide (``hidden_by: user``) or show one device; its entities follow with ``hidden_by: device``.

    An entity a person hid on its own (``hidden_by: user``) stays hidden when the device is shown again. A combined
    device's copies are refused (409): the combination decides which copy shows.
    """
    hub = runtime.hub
    dev = hub.devices.get(device_id)
    if dev is None:
        raise entries.EntryError("No such device", 404)
    if not isinstance(hidden, bool):
        raise entries.EntryError("hidden must be true or false")
    want = HIDDEN_BY_USER if hidden else None
    if dev.hidden_by != want:
        _refuse_if_combined(runtime, device_id)
    _set_hidden(runtime, dev, want)
    return {"id": dev.id, "name": dev.display_name, "hidden": bool(dev.hidden_by), "hidden_by": dev.hidden_by}


def _domain_arg(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise entries.EntryError(f"{key} must be an integration's domain")
    return value.strip()


def hide_copies(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Hide one integration's copy in every duplicate group (a combined group is left to its combination).

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
        if g["combined"] or (keep and keep not in domains) or (hide and hide not in domains):
            continue
        for r in g["devices"]:
            if (r["domain"] == hide) if hide else (r["domain"] != keep):
                targets.append(r["id"])
    changed = [i for i in targets if not hub.devices[i].hidden_by]
    for device_id in changed:
        set_device_hidden(runtime, device_id, True)
    return {"changed": changed, **list_duplicates(runtime)}


def show_copies(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    """``{"domain": "matter"}``: show again every hidden copy of that integration in a duplicate group.

    A combined device's fallback stays hidden: uncombining is what brings it back.
    """
    domain = _domain_arg(body, "domain") or _domain_arg(body, "hide_domain")
    if not domain:
        raise entries.EntryError("Say which integration's copies to show (domain)")
    hub = runtime.hub
    changed = [r["id"] for g in list_duplicates(runtime)["groups"] for r in g["devices"]
               if r["domain"] == domain and not g["combined"] and hub.devices[r["id"]].hidden_by]
    for device_id in changed:
        set_device_hidden(runtime, device_id, False)
    return {"changed": changed, **list_duplicates(runtime)}


# -- combining ------------------------------------------------------------------------------------------

def _carry(changes: dict[str, list[Any]], obj: Any, field: str, value: Any) -> None:
    """Set a field, remembering ``[before, after]`` so uncombining can put it back."""
    before = getattr(obj, field)
    if before == value:
        return
    changes[field] = [before, value]
    setattr(obj, field, value)


def _combine_one(runtime: Any, main_id: str, fallback_id: str) -> dict[str, Any]:
    """Combine two copies of one device. Answers the new record; nothing is saved when it refuses."""
    hub = runtime.hub
    main, fallback = hub.devices.get(main_id), hub.devices.get(fallback_id)
    if main is None or fallback is None:
        raise entries.EntryError("No such device", 404)
    if main_id == fallback_id:
        raise entries.EntryError("A device cannot be its own fallback")
    for dev in (main, fallback):
        if record_of(runtime, dev.id) is not None:
            raise entries.EntryError(f"{dev.display_name or dev.id} is already combined. Uncombine it first.", 409)
    if not any({main_id, fallback_id} <= set(g["ids"]) for g in duplicate_groups(hub)):
        raise entries.EntryError("These two devices are not copies of one device (no shared network address, "
                                 "serial number, or name in one room)")
    pairs = entity_pairs(hub, main_id, fallback_id)
    if not pairs:
        raise entries.EntryError("These two copies have nothing to combine: no light, switch or other control "
                                 "that both of them have exactly one of")
    restore: dict[str, Any] = {
        "devices": {d.id: {"hidden_by": d.hidden_by} for d in (main, fallback)},
        "entities": {eid: {"hidden_by": hub.entities[eid].hidden_by}
                     for d in (main, fallback) for eid in hub.device_entities(d.id)},
        "set": {"devices": {}, "entities": {}},
    }
    # Carry over what only the other copy has: the person's name for it, its room, its labels.
    dev_changes: dict[str, list[Any]] = {}
    if not main.name_by_user and fallback.name_by_user:
        _carry(dev_changes, main, "name_by_user", fallback.name_by_user)
    if not main.area_id and fallback.area_id:
        _carry(dev_changes, main, "area_id", fallback.area_id)
    if any(lb not in main.labels for lb in fallback.labels):
        _carry(dev_changes, main, "labels", [*main.labels, *(lb for lb in fallback.labels if lb not in main.labels)])
    if dev_changes:
        restore["set"]["devices"][main.id] = dev_changes
    for m_eid, f_eid in pairs:
        m_ent, f_ent = hub.entities[m_eid], hub.entities[f_eid]
        changes: dict[str, list[Any]] = {}
        if _generic(hub.friendly_name(m_ent), m_ent.domain):
            # "Light" names nothing: take the other copy's own name for it, else the device's name.
            better = f_ent.name if f_ent.name and not _generic(f_ent.name, f_ent.domain) else None
            _carry(changes, m_ent, "name", better)
            if better is None and _generic(hub.friendly_name(m_ent), m_ent.domain) and main.display_name:
                _carry(changes, m_ent, "name", main.display_name)
        if not m_ent.area_id and f_ent.area_id and not main.area_id:
            _carry(changes, m_ent, "area_id", f_ent.area_id)
        for field in ("labels", "aliases"):
            extra = [x for x in getattr(f_ent, field) if x not in getattr(m_ent, field)]
            if extra:
                _carry(changes, m_ent, field, [*getattr(m_ent, field), *extra])
        if changes:
            restore["set"]["entities"][m_eid] = changes
            runtime.store.save_entity(m_ent)
            hub.bus.fire("entity_registry_updated", {"action": "update", "entity_id": m_eid})
    if dev_changes:
        runtime.store.save_device(main)
        hub.bus.fire("device_registry_updated", {"action": "update", "device_id": main.id})
    # The main shows (a copy a person hid before is the main now); the fallback is kept, hidden.
    _set_hidden(runtime, main, None)
    _set_hidden(runtime, fallback, HIDDEN_BY_COMBINED)
    record = {"main": main_id, "fallback": fallback_id, "pairs": pairs, "restore": restore,
              "created": utcnow().isoformat()}
    for m_eid, f_eid in pairs:  # each copy's own state so far, before the two ids start sharing one
        for eid in (m_eid, f_eid):
            st = hub.states.get(eid)
            if st is not None:
                hub.copy_states[eid] = st
    log.info("combined %s (main, %s) with %s (fallback, %s): %s", main.display_name, _domain(hub, main),
             fallback.display_name, _domain(hub, fallback), ", ".join(f"{m} + {f}" for m, f in pairs))
    return record


def _commit(runtime: Any, records: list[dict[str, Any]], touched: list[dict[str, Any]]) -> None:
    hub = runtime.hub
    _save_records(runtime, records)
    apply_combinations(hub, records)
    for r in touched:
        ids = [hub.device_entities(r["main"]), hub.device_entities(r["fallback"])]
        _republish(hub, [e for group in ids for e in group])


def _device_arg(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise entries.EntryError(f"{key} must be a device id")
    return value.strip()


def _group_domains(hub: Hub, ids: list[str]) -> dict[str, str]:
    """``domain -> device id`` for a two-copy group."""
    out = {}
    for i in ids:
        d = _domain(hub, hub.devices[i])
        if d:
            out[d] = i
    return out


def combine(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Combine duplicates into one device with a main and a fallback.

    - ``{"main": id, "fallback": id}``: these two copies.
    - ``{"group": id, "main_domain"?: d}``: that group's two copies, the main from ``main_domain`` or else the
      household rule for its two integrations (refused when neither says).
    - ``{"main_domain": a, "fallback_domain": b}``: every group of exactly those two integrations that is not
      combined yet, and the rule is saved as the household's ("Prefer a, fall back to b").

    Answers ``{"combined": [main ids], "skipped": [{"id", "reason"}], **list_duplicates}``.
    """
    hub = runtime.hub
    records = _records(runtime)
    main_id, fallback_id = _device_arg(body, "main"), _device_arg(body, "fallback")
    group_id = _device_arg(body, "group")
    main_domain, fallback_domain = _domain_arg(body, "main_domain"), _domain_arg(body, "fallback_domain")
    made: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    if main_id or fallback_id:
        if not (main_id and fallback_id):
            raise entries.EntryError("Say which copy is the main and which is the fallback")
        made.append(_combine_one(runtime, main_id, fallback_id))
    elif group_id:
        group = next((g for g in duplicate_groups(hub) if group_id in g["ids"]), None)
        if group is None:
            raise entries.EntryError("No such duplicate group", 404)
        if len(group["ids"]) != 2:
            raise entries.EntryError("Only a device seen exactly twice can be combined; pick the two copies")
        by_domain = _group_domains(hub, group["ids"])
        want = main_domain or _rules(runtime).get(_rule_key(*by_domain)) if len(by_domain) == 2 else None
        if want not in by_domain:
            raise entries.EntryError("Say which integration should be the main (main_domain)")
        other = next(d for d in by_domain if d != want)
        made.append(_combine_one(runtime, by_domain[want], by_domain[other]))
    elif main_domain and fallback_domain:
        if main_domain == fallback_domain:
            raise entries.EntryError("main_domain and fallback_domain must be different integrations")
        set_rule(runtime, {"main_domain": main_domain, "fallback_domain": fallback_domain})
        for g in duplicate_groups(hub):
            by_domain = _group_domains(hub, g["ids"])
            if len(g["ids"]) != 2 or set(by_domain) != {main_domain, fallback_domain}:
                continue
            if any(record_of(runtime, i) for i in g["ids"]):
                continue
            try:
                made.append(_combine_one(runtime, by_domain[main_domain], by_domain[fallback_domain]))
            except entries.EntryError as exc:
                skipped.append({"id": by_domain[main_domain], "reason": str(exc)})
            else:
                _commit(runtime, [*records, *made], [])
    else:
        raise entries.EntryError("Say which copies to combine (main and fallback), a group, or two integrations")
    records = [*records, *made]
    _commit(runtime, records, made)
    return {"combined": [r["main"] for r in made], "skipped": skipped, **list_duplicates(runtime)}


def _restore(runtime: Any, r: dict[str, Any]) -> None:
    """Put both copies back as they were before combining (a field a person changed since stays theirs)."""
    hub = runtime.hub
    restore = r.get("restore") or {}
    sets = restore.get("set") or {}
    for dev_id, changes in (sets.get("devices") or {}).items():
        dev = hub.devices.get(dev_id)
        if dev is None:
            continue
        for field, (before, after) in changes.items():
            if getattr(dev, field) == after:
                setattr(dev, field, before)
        runtime.store.save_device(dev)
        hub.bus.fire("device_registry_updated", {"action": "update", "device_id": dev.id})
    for eid, changes in (sets.get("entities") or {}).items():
        ent = hub.entities.get(eid)
        if ent is None:
            continue
        for field, (before, after) in changes.items():
            if getattr(ent, field) == after:
                setattr(ent, field, before)
        runtime.store.save_entity(ent)
        hub.bus.fire("entity_registry_updated", {"action": "update", "entity_id": eid})
    ent_hidden = {eid: v.get("hidden_by") for eid, v in (restore.get("entities") or {}).items()}
    for dev_id in (r["main"], r["fallback"]):
        dev = hub.devices.get(dev_id)
        if dev is None:
            continue
        before = ((restore.get("devices") or {}).get(dev_id) or {}).get("hidden_by")
        if before == HIDDEN_BY_COMBINED:
            before = None
        _set_hidden(runtime, dev, before, {e: v for e, v in ent_hidden.items() if e in hub.entities
                                           and hub.entities[e].device_id == dev_id})


def _own_states_back(hub: Hub, r: dict[str, Any]) -> None:
    """Each copy's id shows its own state again (what its driver last said)."""
    for pair in r.get("pairs") or []:
        for eid in pair:
            own = hub.copy_states.pop(eid, None)
            ent = hub.entities.get(eid)
            if own is None or ent is None:
                continue
            attrs = dict(own.attributes)
            attrs["friendly_name"] = hub.friendly_name(ent)
            hub.states.set(eid, own.state, attrs)


def _record_arg(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    device_id = _device_arg(body, "device") or _device_arg(body, "main")
    if not device_id:
        raise entries.EntryError("Say which combined device (device)")
    r = record_of(runtime, device_id)
    if r is None:
        raise entries.EntryError("That device is not combined", 404)
    return r


def uncombine(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    """``{"device": id}`` (either copy): both copies back as they were. Answers ``{"uncombined", **list}``."""
    hub = runtime.hub
    r = _record_arg(runtime, body)
    records = [x for x in _records(runtime) if x is not r and x != r]
    _save_records(runtime, records)
    copies = {eid: hub.copy_states.get(eid) for pair in r.get("pairs") or [] for eid in pair}
    apply_combinations(hub, records)
    hub.copy_states.update({k: v for k, v in copies.items() if v is not None})
    _restore(runtime, r)
    _own_states_back(hub, r)
    _republish(hub, [*hub.device_entities(r["main"]), *hub.device_entities(r["fallback"])])
    log.info("uncombined %s and %s", r["main"], r["fallback"])
    return {"uncombined": r["main"], **list_duplicates(runtime)}


def swap(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    """``{"device": id}``: the fallback becomes the main. Answers ``{"combined": [new main], **list}``."""
    r = _record_arg(runtime, body)
    uncombine(runtime, {"device": r["main"]})
    try:
        return combine(runtime, {"main": r["fallback"], "fallback": r["main"]})
    except entries.EntryError:
        combine(runtime, {"main": r["main"], "fallback": r["fallback"]})  # put it back the way it was
        raise


def set_rule(runtime: Any, body: dict[str, Any]) -> dict[str, Any]:
    """``{"main_domain", "fallback_domain"}``: the household's main for that pair of integrations, used when a
    pair is combined without saying which is the main; ``{"clear": [a, b]}`` removes it. Answers the list."""
    rules = _rules(runtime)
    clear = body.get("clear")
    if clear is not None:
        if not (isinstance(clear, list) and len(clear) == 2 and all(isinstance(x, str) and x for x in clear)):
            raise entries.EntryError("clear must name two integrations")
        rules.pop(_rule_key(clear[0], clear[1]), None)
    else:
        main, fallback = _domain_arg(body, "main_domain"), _domain_arg(body, "fallback_domain")
        if not main or not fallback:
            raise entries.EntryError("Say which integration is the main (main_domain) and the fallback "
                                     "(fallback_domain)")
        if main == fallback:
            raise entries.EntryError("main_domain and fallback_domain must be different integrations")
        rules[_rule_key(main, fallback)] = main
    runtime.store.set_kv(RULES_KEY, rules)
    return list_duplicates(runtime)
