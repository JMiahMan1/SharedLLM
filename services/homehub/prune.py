"""Pruning stale registry data carried over from Home Assistant.

The import copied HA's whole registry, including integrations, devices and
entities nobody has enabled or heard from in years. This module finds them
(a dry run that changes nothing) and removes exactly what a reviewed dry run
listed.

What "activity" means
---------------------
An item's last activity is the newest of the timestamps the hub actually has:

- the entity's current state ``last_updated`` (live hub) or its stored last
  state (``last_states``), when the state is a real value;
- its newest ``history`` row with a real value;
- its newest long-term statistics row imported from HA or written by a driver
  (``origin`` imported/external). Rows the hub compiles itself are left out:
  they are derived from the hub's own states, and a restored value that no
  driver updates would otherwise be compiled into a fresh row every five
  minutes and look alive forever;
- the registry ``modified_at`` HA kept for the entity, device or config entry,
  read (read-only) from the imported HA config directory when it is there.

``unavailable`` and ``unknown`` are not activity: every restart writes them
for every device that does not answer.

A device's activity includes all of its entities; an entry's includes all of
its devices and entities. A missing timestamp counts as older than any cutoff.

The rules
---------
- **Config entry**: disabled (``disabled_by``) with no activity since the
  cutoff, or of an integration the hub has no driver for and no activity since
  the cutoff. Removing it removes its entities and every device left with no
  entry (``entries.remove_entry``, which also releases a HomeKit pairing).
- **Device**: disabled with no activity since the cutoff, or every entity
  silent since the cutoff, or no entities at all, a registry change older than
  the cutoff and no loaded entry. Removed from the registry with its entities.
- **Entity**: disabled with no activity since the cutoff, or orphaned (no
  device, no config entry, so no driver) with no activity since the cutoff.
- **Automation / script**: disabled (an automation turned off, a script whose
  registry entity is disabled) and unmodified since the cutoff (last
  triggered and registry change both older), and every
  entity and device it names is being removed or no longer exists. One that
  names some removed items and some kept ones is listed under ``review`` for
  a person and not deleted.

Never removed (listed under ``kept`` with the reason when a rule matched):
anything with activity inside the cutoff; an entity a loaded driver is
publishing a real value for; persons, zones, areas, users and tokens (this
module never touches those tables); anything an enabled automation, a script
that is not being removed, a scene, a schedule, a person, a zone or the energy
settings names; a device with a kept entity or a kept child device; an entry
with a kept device or entity; ignored discoveries (``source: ignore``); and
the hub's own entries (``scheduler``).

History and statistics rows of removed entities are kept by default (HA's
history stays usable); ``drop_history`` deletes them too.

The plan id is a hash of the cutoff, the ``drop_history`` flag and every item
with its reason. Apply rebuilds the plan with the reviewed cutoff and refuses
(409) when the id differs, so what is removed is exactly what was reviewed.

This module writes only to the store and statistics database it is given. The
HA config directory is opened read-only for timestamps and never written.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from services.homehub.core import ConfigEntry, DeviceEntry, EntityEntry, HubError, State

log = logging.getLogger("homehub.prune")

DEFAULT_DAYS = 365
#: The shortest window a plan may use; anything newer is too close to "in use".
MIN_DAYS = 30
NOT_ACTIVITY = {"unavailable", "unknown", ""}
#: Entity domains this module never removes as entities (automation/script go through their own rule).
PROTECTED_ENTITY_DOMAINS = {"person", "zone", "scene", "automation", "script"}
#: Entries the hub itself uses without a driver.
PROTECTED_ENTRY_DOMAINS = {"scheduler"}
#: Statistics origins that count as activity (see the module docstring).
STAT_ORIGINS_COUNTED = (1, 2)

_ENTITY_TOKEN = re.compile(r"\b([a-z_][a-z0-9_]*\.[a-z0-9_]+)\b")
_LOCK = asyncio.Lock()


class PruneError(HubError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Item:
    """An automation or script as the planner sees it."""

    kind: str
    id: str
    alias: str
    entity_id: str | None
    enabled: bool
    last_triggered: float | None
    configs: list[Any]


@dataclass
class Snapshot:
    entries: dict[str, ConfigEntry]
    devices: dict[str, DeviceEntry]
    entities: dict[str, EntityEntry]
    states: dict[str, State]
    #: Entries whose driver is running now.
    loaded_entries: set[str]
    history_last: dict[str, float]
    stats_last: dict[str, float]
    #: {"entity"|"device"|"entry": {id: modified_at ts}} from HA's registry, when available.
    registry_times: dict[str, dict[str, float]] | None
    items: list[Item]
    #: Configurations whose every reference is kept (scenes, schedules, persons, zones, energy).
    protecting: list[tuple[str, Any]] = field(default_factory=list)
    registry_ids: dict[str, str] = field(default_factory=dict)
    has_driver: Any = None

    def driver_exists(self, domain: str) -> bool:
        if self.has_driver is not None:
            return bool(self.has_driver(domain))
        from services.homehub.drivers import DRIVERS

        return domain in DRIVERS


# -- timestamps ------------------------------------------------------------------------------

def _ts(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, UTC).isoformat() if ts is not None else None


def _newest(*values: float | None) -> float | None:
    vals = [v for v in values if v is not None]
    return max(vals) if vals else None


def ha_registry_times(config_dir: str | None) -> dict[str, dict[str, float]] | None:
    """``modified_at`` of HA's entities, devices and entries; ``None`` when there is no HA config.

    Opens three registry files read-only and keeps only ids and timestamps
    (never entry data, which holds credentials).
    """
    if not config_dir or not os.path.isdir(os.path.join(config_dir, ".storage")):
        return None
    out: dict[str, dict[str, float]] = {"entity": {}, "device": {}, "entry": {}}
    spec = (("core.entity_registry", "entities", "entity", "entity_id"),
            ("core.device_registry", "devices", "device", "id"),
            ("core.config_entries", "entries", "entry", "entry_id"))
    for name, key, kind, id_key in spec:
        path = os.path.join(config_dir, ".storage", name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            data = (json.load(f) or {}).get("data") or {}
        for item in data.get(key) or []:
            ts = _ts(item.get("modified_at"))
            if item.get(id_key) and ts is not None:
                out[kind][str(item[id_key])] = ts
    return out


_HISTORY_SQL = ("SELECT entity_id, MAX(last_updated) FROM history "
                "WHERE state NOT IN ('unavailable', 'unknown', '') GROUP BY entity_id")
_STATS_SQL = ("SELECT m.statistic_id, MAX(s.start_ts) FROM {table} s JOIN statistics_meta m ON m.id = s.metadata_id "
              "WHERE s.origin IN ({origins}) GROUP BY m.statistic_id")


def _stats_last(execute: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    origins = ",".join(str(o) for o in STAT_ORIGINS_COUNTED)
    for table in ("statistics", "statistics_short_term"):
        for sid, ts in execute(_STATS_SQL.format(table=table, origins=origins)).fetchall():
            if ts is not None and (sid not in out or ts > out[sid]):
                out[sid] = float(ts)
    return out


# -- references -------------------------------------------------------------------------------

def _strings(value: Any, key: str | None = None) -> Iterable[tuple[str, str | None]]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k), None  # a scene's ``entities`` is keyed by entity id
            yield from _strings(v, str(k))
    elif isinstance(value, (list, tuple, set)):
        for v in value:
            yield from _strings(v, key)
    elif isinstance(value, str):
        yield value, key


def references(conf: Any, snap: Snapshot) -> tuple[set[str], set[str]]:
    """(entity ids, device ids) a configuration names, templates included.

    Anything that looks like an entity id and is in the registry counts, as do
    literal ``entity_id``/``device_id`` values that are not (missing ones), and
    HA registry uuids, mapped to their entity.
    """
    ents: set[str] = set()
    devs: set[str] = set()
    for text, key in _strings(conf):
        if text in snap.devices:
            devs.add(text)
        elif key in ("device_id", "device") and text.strip():
            devs.add(text.strip())
        if text in snap.registry_ids:
            ents.add(snap.registry_ids[text])
        for token in _ENTITY_TOKEN.findall(text):
            if token in snap.entities or (key is not None and (key == "entity_id" or key.endswith("_entity_id"))):
                ents.add(token)
    return ents, devs


# -- snapshots --------------------------------------------------------------------------------

def _automation_entity(entities: dict[str, EntityEntry], kind: str, item_id: str) -> str | None:
    for e in entities.values():
        if e.platform == kind and str(e.unique_id) == item_id:
            return e.entity_id
    return None


def _items_from_store(rows: dict[str, list[dict[str, Any]]], kv: Any, entities: dict[str, EntityEntry],
                      states: dict[str, State], remap: Any) -> list[Item]:
    """Automations and scripts as the engine would load them, without starting it."""
    from services.homehub.automation_editor import apply_remap, normalise_remap

    remap_doc = normalise_remap(remap or {})
    out = []
    for kind in ("automation", "script"):
        for doc in rows.get(kind, []):
            item_id = str(doc.get("id"))
            eid = _automation_entity(entities, kind, item_id)
            saved = kv(f"{kind}:{item_id}") or {}
            st = states.get(eid or "")
            if kind == "automation":
                if "enabled" in saved:
                    enabled = bool(saved["enabled"])
                elif "initial_state" in doc:
                    enabled = bool(doc["initial_state"])
                else:
                    enabled = not (st is not None and st.state == "off")
            else:
                ent = entities.get(eid or "")
                enabled = not (ent is not None and ent.disabled_by)
            last = _ts(saved.get("last_triggered") or (st.attributes.get("last_triggered") if st else None))
            conf = {k: v for k, v in doc.items() if not (kind == "script" and k == "id")}
            remapped = apply_remap(remap_doc, kind, item_id, conf)[0] if remap_doc else conf
            out.append(Item(kind, item_id, str(doc.get("alias") or item_id), eid, enabled, last, [doc, remapped]))
    return out


def _protecting_from_kv(kv: Any) -> list[tuple[str, Any]]:
    return [("person", kv("persons") or []), ("zone", kv("zones") or []), ("schedule", kv("scheduler") or {}),
            ("energy settings", kv("energy_prefs") or {})]


def snapshot_from_runtime(runtime: Any, import_dir: str | None = None) -> Snapshot:
    """The live hub: its registry, states, running drivers and automation engine."""
    hub, store = runtime.hub, runtime.store
    history_last = {eid: float(ts) for eid, ts in store._exec(_HISTORY_SQL).fetchall() if ts is not None}
    stats = getattr(runtime, "statistics", None)
    stats_last = _stats_last(stats._exec) if stats is not None else {}
    states = {s.entity_id: s for s in hub.states.all()}
    engine = getattr(runtime, "automations", None)
    stored = {k: store.load_automations(k) for k in ("automation", "script", "scene")}
    if engine is not None:
        items = []
        stored_by = {(k, str(d.get("id"))): d for k in ("automation", "script") for d in stored[k]}
        for kind, pool in (("automation", engine.automations), ("script", engine.scripts)):
            for it in pool.values():
                if kind == "automation":
                    enabled = bool(it.enabled)
                else:
                    ent = hub.entities.get(it.entity_id)
                    enabled = not (ent is not None and ent.disabled_by)
                confs = [it.config] + ([stored_by[(kind, it.id)]] if (kind, it.id) in stored_by else [])
                items.append(Item(kind, it.id, it.alias, it.entity_id, enabled, _ts(it.last_triggered), confs))
        scenes = [("scene", {"entities": dict(s.entities), "id": s.id}) for s in engine.scenes.values()]
    else:
        items = _items_from_store(stored, store.get_kv, hub.entities, states, store.get_kv("automation_remap"))
        scenes = []
    protecting = [*scenes, *(("scene", d) for d in stored["scene"]), *_protecting_from_kv(store.get_kv)]
    return Snapshot(
        entries=dict(hub.config_entries), devices=dict(hub.devices), entities=dict(hub.entities), states=states,
        loaded_entries={eid for eid in hub.drivers if (e := hub.config_entries.get(eid)) is not None
                        and e.state == "loaded"},
        history_last=history_last, stats_last=stats_last, registry_times=ha_registry_times(import_dir),
        items=items, protecting=protecting, registry_ids=dict(hub.entity_registry_ids or {}),
    )


def _ro(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        raise PruneError(f"{path} does not exist", 404)
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def snapshot_from_files(db_path: str, stats_path: str | None, import_dir: str | None,
                        loaded_entries: Iterable[str]) -> Snapshot:
    """A copy of the hub's database, opened read-only (for a dry run away from the hub).

    ``loaded_entries`` are the entries whose driver runs on the live hub (``GET /hub/status``),
    since a file cannot say which drivers are connected. Entry credentials are never opened.
    """
    from services.homehub.store import _load

    db = _ro(db_path)
    try:
        def rows(sql: str) -> list[tuple]:
            return db.execute(sql).fetchall()

        kv_cache = {k: json.loads(v) for k, v in rows("SELECT key, value FROM kv")}

        def kv(key: str, default: Any = None) -> Any:
            return kv_cache.get(key, default)

        entries = {}
        for (doc,) in rows("SELECT doc FROM config_entries"):
            data = json.loads(doc)
            data["data"] = {}
            entries[data["entry_id"]] = _load(ConfigEntry, json.dumps(data))
        devices = {d.id: d for d in (_load(DeviceEntry, r[0]) for r in rows("SELECT doc FROM devices"))}
        entities = {e.entity_id: e for e in (_load(EntityEntry, r[0]) for r in rows("SELECT doc FROM entities"))}
        states = {s.entity_id: s for s in (State.from_dict(json.loads(r[0])) for r in rows("SELECT doc FROM last_states"))}
        history_last = {eid: float(ts) for eid, ts in rows(_HISTORY_SQL) if ts is not None}
        stored: dict[str, list[dict[str, Any]]] = {}
        for kind, doc in rows("SELECT kind, doc FROM automations ORDER BY id"):
            stored.setdefault(kind, []).append(json.loads(doc))
    finally:
        db.close()
    stats_last: dict[str, float] = {}
    if stats_path:
        sdb = _ro(stats_path)
        try:
            stats_last = _stats_last(sdb.execute)
        finally:
            sdb.close()
    items = _items_from_store(stored, kv, entities, states, kv("automation_remap"))
    protecting = [*(("scene", d) for d in stored.get("scene", [])), *_protecting_from_kv(kv)]
    return Snapshot(entries=entries, devices=devices, entities=entities, states=states,
                    loaded_entries=set(loaded_entries), history_last=history_last, stats_last=stats_last,
                    registry_times=ha_registry_times(import_dir), items=items, protecting=protecting,
                    registry_ids=kv("entity_registry_ids", {}) or {})


# -- the planner ------------------------------------------------------------------------------

def _real(st: State | None) -> bool:
    return st is not None and st.state not in NOT_ACTIVITY


def build_plan(snap: Snapshot, cutoff: float, *, drop_history: bool = False) -> dict[str, Any]:
    """The dry run: what would be removed, what was kept and why, and the plan id. Changes nothing."""
    reg = snap.registry_times or {}
    reg_e, reg_d, reg_x = reg.get("entity", {}), reg.get("device", {}), reg.get("entry", {})

    def stale(ts: float | None) -> bool:
        return ts is None or ts < cutoff

    # Activity ----------------------------------------------------------------------------
    act_e: dict[str, float | None] = {}
    for eid in snap.entities:
        st = snap.states.get(eid)
        act_e[eid] = _newest(st.last_updated.timestamp() if _real(st) else None,
                             snap.history_last.get(eid), snap.stats_last.get(eid), reg_e.get(eid))
    dev_ents: dict[str, list[str]] = {}
    entry_ents: dict[str, list[str]] = {}
    for e in snap.entities.values():
        if e.device_id:
            dev_ents.setdefault(e.device_id, []).append(e.entity_id)
        if e.config_entry_id:
            entry_ents.setdefault(e.config_entry_id, []).append(e.entity_id)
    act_d = {d.id: _newest(reg_d.get(d.id), *(act_e[x] for x in dev_ents.get(d.id, [])))
             for d in snap.devices.values()}
    entry_devs: dict[str, list[str]] = {}
    for d in snap.devices.values():
        for x in d.config_entries:
            entry_devs.setdefault(x, []).append(d.id)
    act_x = {x.entry_id: _newest(reg_x.get(x.entry_id), *(act_e[e] for e in entry_ents.get(x.entry_id, [])),
                                 *(act_d[d] for d in entry_devs.get(x.entry_id, [])))
             for x in snap.entries.values()}

    # Protection from references and live drivers ---------------------------------------------
    prot_e: dict[str, str] = {}
    prot_d: dict[str, str] = {}
    prot_x: dict[str, str] = {}

    def protect_refs(conf: Any, why: str) -> None:
        ents, devs = references(conf, snap)
        for eid in ents:
            prot_e.setdefault(eid, why)
        for did in devs:
            prot_d.setdefault(did, why)

    for it in snap.items:
        if it.enabled:
            for conf in it.configs:
                protect_refs(conf, f"named by enabled {it.kind} {it.alias!r}")
    for label, conf in snap.protecting:
        protect_refs(conf, f"named by a {label}")
    for e in snap.entities.values():
        st = snap.states.get(e.entity_id)
        if e.domain in PROTECTED_ENTITY_DOMAINS:
            prot_e.setdefault(e.entity_id, f"{e.domain} entities are never pruned here")
        elif not e.disabled_by and e.config_entry_id in snap.loaded_entries and _real(st):
            prot_e.setdefault(e.entity_id, "a loaded driver publishes it")
    for x in snap.entries.values():
        if x.source == "ignore":
            prot_x.setdefault(x.entry_id, "an ignored discovery")
        if x.domain in PROTECTED_ENTRY_DOMAINS:
            prot_x.setdefault(x.entry_id, "used by the hub itself")

    # Raw candidates --------------------------------------------------------------------------
    raw_e: dict[str, str] = {}
    for e in snap.entities.values():
        if e.domain in PROTECTED_ENTITY_DOMAINS or not stale(act_e[e.entity_id]):
            continue
        orphan = (not e.device_id or e.device_id not in snap.devices) and \
                 (not e.config_entry_id or e.config_entry_id not in snap.entries)
        if e.disabled_by:
            raw_e[e.entity_id] = "entity_disabled"
        elif orphan:
            raw_e[e.entity_id] = "entity_orphaned"
    raw_d: dict[str, str] = {}
    for d in snap.devices.values():
        if not stale(act_d[d.id]):
            continue
        ents = dev_ents.get(d.id, [])
        if d.disabled_by:
            raw_d[d.id] = "device_disabled"
        elif ents:
            raw_d[d.id] = "device_silent"
        elif d.id in reg_d and not any(x in snap.loaded_entries for x in d.config_entries):
            raw_d[d.id] = "device_empty"
    raw_x: dict[str, str] = {}
    for x in snap.entries.values():
        if not stale(act_x[x.entry_id]):
            continue
        if x.disabled_by:
            raw_x[x.entry_id] = "entry_disabled"
        elif not snap.driver_exists(x.domain):
            raw_x[x.entry_id] = "entry_no_driver"

    # Propagate protection until nothing changes -----------------------------------------------
    children: dict[str, list[str]] = {}
    for d in snap.devices.values():
        if d.via_device_id:
            children.setdefault(d.via_device_id, []).append(d.id)
    while True:
        changed = False

        def mark(table: dict[str, str], key: str | None, why: str) -> None:
            nonlocal changed
            if key and key not in table:
                table[key] = why
                changed = True

        for eid, why in list(prot_e.items()):
            e = snap.entities.get(eid)
            if e is not None:
                mark(prot_d, e.device_id, f"has a kept entity ({eid}: {why})")
                mark(prot_x, e.config_entry_id, f"has a kept entity ({eid}: {why})")
        for did in list(prot_d):
            d = snap.devices.get(did)
            if d is None:
                continue
            for x in d.config_entries:
                mark(prot_x, x, f"has a kept device ({d.display_name or did})")
            mark(prot_d, d.via_device_id, f"parent of a kept device ({d.display_name or did})")
        rm_x = {x for x in raw_x if x not in prot_x}
        rm_d = {d for d in raw_d if d not in prot_d}
        rm_d |= {d.id for d in snap.devices.values() if d.config_entries and set(d.config_entries) <= rm_x}
        for did in list(rm_d):
            for child in children.get(did, []):
                if child not in rm_d:
                    mark(prot_d, did, f"parent of a kept device ({child})")
        rm_e = {e for e in raw_e if e not in prot_e}
        rm_e |= {e.entity_id for e in snap.entities.values()
                 if e.device_id in rm_d or e.config_entry_id in rm_x}
        for eid in rm_e:
            if eid in prot_e:
                e = snap.entities[eid]
                mark(prot_d, e.device_id if e.device_id in rm_d else None, f"has a kept entity ({eid})")
                mark(prot_x, e.config_entry_id if e.config_entry_id in rm_x else None, f"has a kept entity ({eid})")
        if not changed:
            break

    # Automations and scripts -----------------------------------------------------------------
    rm_items: list[tuple[Item, str]] = []
    review: list[dict[str, Any]] = []
    for it in snap.items:
        if it.enabled:
            continue
        # Not its state's last_changed: the engine publishes every item when it loads, so a hub restart
        # would look like a change.
        last = _newest(it.last_triggered, reg_e.get(it.entity_id or ""))
        if not stale(last):
            continue
        ents: set[str] = set()
        devs: set[str] = set()
        for conf in it.configs:
            e_, d_ = references(conf, snap)
            ents |= e_
            devs |= d_
        gone_e = {e for e in ents if e in rm_e or e not in snap.entities}
        gone_d = {d for d in devs if d in rm_d or d not in snap.devices}
        removed_named = (ents & rm_e) | (devs & rm_d)
        if (ents or devs) and gone_e == ents and gone_d == devs:
            rm_items.append((it, "disabled_names_only_removed_or_missing"))
        elif removed_named:
            review.append({"kind": it.kind, "id": it.id, "name": it.alias, "entity_id": it.entity_id,
                           "reason": "references_stale_entities", "last_activity": _iso(last),
                           "removed_references": sorted(removed_named),
                           "kept_references": sorted((ents - gone_e) | (devs - gone_d))})

    # The plan ---------------------------------------------------------------------------------
    plan_items: list[dict[str, Any]] = []
    for xid in sorted(rm_x):
        x = snap.entries[xid]
        plan_items.append({"kind": "config_entry", "id": xid, "name": x.title, "domain": x.domain,
                           "reason": raw_x[xid], "last_activity": _iso(act_x[xid])})
    for did in sorted(rm_d):
        d = snap.devices[did]
        reason = "with_entry" if d.config_entries and set(d.config_entries) <= rm_x else raw_d[did]
        plan_items.append({"kind": "device", "id": did, "name": d.display_name, "reason": reason,
                           "entries": list(d.config_entries), "last_activity": _iso(act_d[did])})
    for eid in sorted(rm_e):
        e = snap.entities[eid]
        if e.config_entry_id in rm_x:
            reason = "with_entry"
        elif e.device_id in rm_d:
            reason = "with_device"
        else:
            reason = raw_e[eid]
        plan_items.append({"kind": "entity", "id": eid, "name": e.name or e.original_name or eid,
                           "platform": e.platform, "device_id": e.device_id, "config_entry_id": e.config_entry_id,
                           "disabled_by": e.disabled_by, "reason": reason, "last_activity": _iso(act_e[eid])})
    for it, reason in rm_items:
        plan_items.append({"kind": it.kind, "id": it.id, "name": it.alias, "entity_id": it.entity_id,
                           "reason": reason, "last_activity": _iso(it.last_triggered)})

    kept: list[dict[str, Any]] = []
    for xid, reason in sorted(raw_x.items()):
        if xid in prot_x:
            kept.append({"kind": "config_entry", "id": xid, "name": snap.entries[xid].title, "matched": reason,
                         "kept_because": prot_x[xid]})
    for did, reason in sorted(raw_d.items()):
        if did in prot_d:
            kept.append({"kind": "device", "id": did, "name": snap.devices[did].display_name, "matched": reason,
                         "kept_because": prot_d[did]})
    for eid, reason in sorted(raw_e.items()):
        if eid in prot_e:
            kept.append({"kind": "entity", "id": eid, "name": snap.entities[eid].name
                         or snap.entities[eid].original_name or eid, "matched": reason, "kept_because": prot_e[eid]})

    counts: dict[str, dict[str, int]] = {}
    for p in plan_items:
        counts.setdefault(p["kind"], {}).setdefault(p["reason"], 0)
        counts[p["kind"]][p["reason"]] += 1
    canon = json.dumps({"cutoff": round(cutoff, 3), "drop_history": bool(drop_history),
                        "items": sorted([p["kind"], p["id"], p["reason"]] for p in plan_items)}, sort_keys=True)
    return {
        "plan_id": hashlib.sha256(canon.encode()).hexdigest()[:24],
        "cutoff": round(cutoff, 3), "cutoff_iso": _iso(cutoff), "drop_history": bool(drop_history),
        "registry_times": "ha_import" if snap.registry_times is not None else "not available",
        "counts": counts, "items": plan_items, "review": review, "kept": kept,
    }


def cutoff_for(days: int, now: float | None = None) -> float:
    if not isinstance(days, int) or isinstance(days, bool) or days < MIN_DAYS:
        raise PruneError(f"days must be a whole number of at least {MIN_DAYS}")
    return (time.time() if now is None else now) - days * 86400


# -- applying ---------------------------------------------------------------------------------

def _delete_rows(execute: Any, sql: str, keys: list[str]) -> int:
    n = 0
    for i in range(0, len(keys), 500):
        chunk = keys[i:i + 500]
        n += execute(sql.format(marks=",".join("?" * len(chunk))), tuple(chunk)).rowcount
    return n


async def apply_plan(runtime: Any, *, plan_id: str, cutoff: float, drop_history: bool = False,
                     import_dir: str | None = None, now: float | None = None) -> dict[str, Any]:
    """Remove exactly what the dry run with this ``plan_id`` listed, or refuse (409)."""
    from services.homehub import entries as entries_mod
    from services.homehub import hardware

    if not isinstance(plan_id, str) or not plan_id:
        raise PruneError("plan_id is required (the plan_id of a dry run)")
    if not isinstance(cutoff, (int, float)) or isinstance(cutoff, bool):
        raise PruneError("cutoff is required (the cutoff of the dry run)")
    if float(cutoff) > (time.time() if now is None else now) - MIN_DAYS * 86400:
        raise PruneError(f"cutoff must be at least {MIN_DAYS} days in the past")
    async with _LOCK:
        snap = snapshot_from_runtime(runtime, import_dir)
        plan = build_plan(snap, float(cutoff), drop_history=bool(drop_history))
        if plan["plan_id"] != plan_id:
            raise PruneError("The hub changed since that dry run (or the cutoff/drop_history differ); "
                             "run the dry run again and review the new plan", 409)
        hub = runtime.hub
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for p in plan["items"]:
            by_kind.setdefault(p["kind"], []).append(p)
        result: dict[str, Any] = {"status": "applied", "plan_id": plan_id, "removed": {}, "warnings": [],
                                  "already_gone": []}
        removed_entities: list[str] = []

        # Automations and scripts first: they name the entities below.
        engine = getattr(runtime, "automations", None)
        editor = getattr(engine, "editor", None) if engine is not None else None
        for kind in ("automation", "script"):
            done = []
            for p in by_kind.get(kind, []):
                if editor is not None:
                    await editor.delete(kind, p["id"])
                else:
                    runtime.store.delete_automation(p["id"], kind)
                    if p.get("entity_id") and p["entity_id"] in hub.entities:
                        entries_mod._drop_entities(runtime, [p["entity_id"]])
                done.append(p["id"])
            result["removed"][kind] = done

        planned_entities = {p["id"] for p in by_kind.get("entity", [])}
        touched_entries: set[str] = set()
        done = []
        for p in by_kind.get("config_entry", []):
            if p["id"] not in hub.config_entries:
                result["already_gone"].append(p["id"])
                continue
            out = await entries_mod.remove_entry(runtime, p["id"], force=True)
            extra = set(out["entities"]) - planned_entities
            if extra:  # cannot happen when the plan is current; say so loudly if it does
                result["warnings"].append(f"{p['name']}: also removed {sorted(extra)}")
            if out.get("warning"):
                result["warnings"].append(f"{p['name']}: {out['warning']}")
            removed_entities += out["entities"]
            done.append(p["id"])
        result["removed"]["config_entry"] = done

        done = []
        for p in by_kind.get("device", []):
            dev = hub.devices.get(p["id"])
            if dev is None:
                continue  # went with its entry
            ents = [eid for eid in hub.device_entities(dev.id) if eid in planned_entities]
            entries_mod._drop_entities(runtime, ents)
            removed_entities += ents
            entries_mod._drop_device(runtime, dev)
            touched_entries.update(dev.config_entries)
            done.append(p["id"])
        result["removed"]["device"] = done

        rest = [p["id"] for p in by_kind.get("entity", []) if p["id"] in hub.entities]
        for eid in rest:
            if hub.entities[eid].config_entry_id:
                touched_entries.add(hub.entities[eid].config_entry_id)
        entries_mod._drop_entities(runtime, rest)
        removed_entities += rest
        result["removed"]["entity"] = sorted(set(removed_entities))

        for xid in sorted(touched_entries):
            if xid in hub.drivers and xid in hub.config_entries:
                await hardware._reload(runtime, hub.config_entries[xid])

        if drop_history and removed_entities:
            keys = sorted(set(removed_entities))
            result["history_rows_deleted"] = _delete_rows(
                runtime.store._exec, "DELETE FROM history WHERE entity_id IN ({marks})", keys)
            stats = getattr(runtime, "statistics", None)
            if stats is not None:
                n = 0
                for table in ("statistics", "statistics_short_term"):
                    n += _delete_rows(stats._exec, f"DELETE FROM {table} WHERE metadata_id IN "
                                      "(SELECT id FROM statistics_meta WHERE statistic_id IN ({marks}))", keys)
                _delete_rows(stats._exec, "DELETE FROM statistics_meta WHERE statistic_id IN ({marks})", keys)
                result["statistics_rows_deleted"] = n
        result["counts"] = {k: len(v) for k, v in result["removed"].items()}
        log.info("prune %s applied: %s", plan_id, result["counts"])
        return result
