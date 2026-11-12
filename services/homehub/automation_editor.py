"""Creating, editing and inspecting automations, scripts, scenes and schedules.

The engine (``automation.py``) runs what the store holds; this module is how
that changes while the hub runs, in place of HA's automation editor and the
nielsfaber scheduler's card:

* :class:`Editor` writes one item at a time (store first, then the engine) and
  rebuilds only that item, so an edit never restarts every other automation's
  ``for:`` timers. New items get a registry entry, as HA gives them, so their
  entity id stays put when they are renamed.
* :class:`Tracer` keeps the last runs of every item (trigger, conditions,
  steps, error) for the run history view.
* :func:`apply_remap` rewrites references to entities and devices that were
  replaced (a new thermostat, a re-paired sensor). The mapping itself is the
  household's data, kept in the store (``automation_remap``) and edited by an
  admin; nothing here knows any entity by name. :func:`suggest_remap` proposes
  one from HA's recorder.
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import copy
import itertools
import logging
import os
import re
import secrets
import sqlite3
import time as _time
from collections import deque
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import yaml

from services.homehub import capabilities
from services.homehub.automation import (
    ALWAYS_KNOWN,
    MODES,
    WEEKDAYS,
    AutomationEngine,
    Item,
    Schedule,
    Unsupported,
    _entity_list,
    slugify,
)
from services.homehub.core import ConfigEntry, EntityEntry, HubError

log = logging.getLogger("homehub.automation_editor")

KINDS = ("automation", "script", "scene")
TRACES_KEPT = 10
REMAP_KEY = "automation_remap"
SCHEDULER_KEY = "scheduler"
SCHEDULE_DAYS = set(WEEKDAYS) | {"daily", "workday", "weekend"}
REPEAT_TYPES = ("repeat", "pause", "single")
_UUID = re.compile(r"^[0-9a-f]{32}$")
_ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")


class EditorError(HubError):
    """A request the editor refuses; ``status`` is the HTTP status the API answers with."""

    def __init__(self, message: str, status: int = 400, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.errors = errors or [message]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _walk_strings(value: Any, fn: Callable[[str, str | None], str], key: str | None = None) -> Any:
    """Copy ``value`` with every string replaced by ``fn(string, its dict key)``."""
    if isinstance(value, dict):
        return {k: _walk_strings(v, fn, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk_strings(v, fn, key) for v in value]
    if isinstance(value, str):
        return fn(value, key)
    return value


def named_entities(conf: Any) -> list[str]:
    """Entity ids a config names literally (for the list view and the remap)."""
    found: list[str] = []

    def visit(v: Any, key: str | None) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                visit(x, str(k))
        elif isinstance(v, list):
            for x in v:
                visit(x, key)
        elif isinstance(v, str) and key and (key == "entity_id" or key.endswith("_entity_id") or key == "zone"):
            for eid in _entity_list(v):
                if _ENTITY_ID.match(eid) and eid not in found:
                    found.append(eid)

    visit(conf, None)
    return found


# -- traces ---------------------------------------------------------------------------------------

_current_trace: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("homehub_trace", default=None)


def _summarise_step(step: dict[str, Any]) -> str:
    kind = step.get("_kind")
    if kind == "service":
        ents: list[str] = []
        for src in (step, step.get("target"), step.get("data")):
            if isinstance(src, dict):
                ents += [str(e) for e in _entity_list(src.get("entity_id"))]
        return f"{step.get('action')}" + (f" on {', '.join(ents)}" if ents else "")
    if kind == "delay":
        return f"wait {step.get('delay')}"
    if kind == "condition":
        return f"check {(step.get('condition') or {}).get('condition', 'condition')}"
    if kind in ("choose", "if", "repeat", "parallel", "sequence"):
        return kind
    if kind == "wait_template":
        return "wait for a template"
    if kind == "wait_for_trigger":
        return "wait for a trigger"
    if kind == "event":
        return f"fire event {step.get('event')}"
    if kind == "scene":
        return f"activate {step.get('scene')}"
    if kind == "stop":
        return f"stop: {step.get('stop')}"
    return str(kind)


def _depths(steps: list[dict[str, Any]], depth: int = 0, out: dict[int, int] | None = None) -> dict[int, int]:
    """id(step) -> nesting depth, so a flat trace can be shown indented."""
    out = {} if out is None else out

    def nested(v: Any, d: int) -> None:
        if isinstance(v, dict):
            if "_kind" in v:
                out.setdefault(id(v), d)
                for k, x in v.items():
                    if k not in ("data", "target", "variables", "condition", "conditions", "if", "while", "until"):
                        nested(x, d + 1)
            else:
                for x in v.values():
                    nested(x, d)
        elif isinstance(v, list):
            for x in v:
                nested(x, d)

    nested(steps, depth)
    return out


def _trigger_summary(trig: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(trig, dict):
        return {"description": "manual"}
    for k, v in trig.items():
        if k in ("from_state", "to_state"):
            out[k] = {"state": getattr(v, "state", None)} if v is not None else None
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
    if not out.get("description"):
        kind = trig.get("trigger") or trig.get("platform")
        ent = trig.get("entity_id")
        to = getattr(trig.get("to_state"), "state", None)
        if kind is None:
            out["description"] = "manual"
        else:
            out["description"] = f"{kind}" + (f" of {ent}" if ent else "") + (f" to {to}" if to else "")
    return out


class Tracer:
    """The last :data:`TRACES_KEPT` runs of each automation and script, kept across restarts."""

    def __init__(self, engine: AutomationEngine, store: Any = None, keep: int = TRACES_KEPT) -> None:
        self.engine = engine
        self.store = store
        self.keep = keep
        self._traces: dict[tuple[str, str], deque[dict[str, Any]]] = {}
        self._ids = itertools.count(1)

    def _bucket(self, item: Item) -> deque[dict[str, Any]]:
        key = (item.kind, item.id)
        if key not in self._traces:
            saved = []
            if self.store is not None:
                with contextlib.suppress(Exception):
                    saved = self.store.get_kv(f"trace:{item.kind}:{item.id}") or []
            self._traces[key] = deque(saved, maxlen=self.keep)
        return self._traces[key]

    def _new(self, item: Item, variables: dict[str, Any]) -> dict[str, Any]:
        return {"run_id": f"{int(_time.time() * 1000)}-{next(self._ids)}", "kind": item.kind, "item_id": item.id,
                "started": self.engine.clock.now().isoformat(), "finished": None, "result": "running",
                "trigger": _trigger_summary(variables.get("trigger")),
                "conditions": {"passed": None, "detail": None}, "steps": [], "error": None}

    def _save(self, item: Item) -> None:
        if self.store is None:
            return
        try:
            self.store.set_kv(f"trace:{item.kind}:{item.id}", list(self._bucket(item)))
        except Exception:
            log.exception("trace of %s could not be saved", item.entity_id)

    def not_run(self, item: Item, variables: dict[str, Any], detail: str) -> None:
        trace = self._new(item, variables)
        trace.update(finished=trace["started"], result="condition_failed",
                     conditions={"passed": False, "detail": detail})
        self._bucket(item).appendleft(trace)
        self._save(item)

    async def run(self, item: Item, variables: dict[str, Any], coro: Any) -> Any:
        trace = self._new(item, variables)
        trace["conditions"]["passed"] = True if item.kind == "automation" else None
        trace["_depths"] = _depths(item.actions)
        self._bucket(item).appendleft(trace)
        token = _current_trace.set(trace)
        # A run that ends on a false condition step leaves last_error alone, so clear it here to tell
        # this run's error from the previous one's.
        item.last_error = None
        try:
            result = await coro
        except asyncio.CancelledError:
            trace["result"] = "cancelled"
            raise
        finally:
            _current_trace.reset(token)
            trace.pop("_depths", None)
            trace["finished"] = self.engine.clock.now().isoformat()
            if trace["result"] == "running":
                trace["result"] = "error" if item.last_error else "finished"
            if item.last_error:
                trace["error"] = item.last_error
                if trace["steps"]:
                    trace["steps"][-1]["error"] = item.last_error
            self._save(item)
        return result

    def step(self, step: dict[str, Any]) -> None:
        trace = _current_trace.get()
        if trace is None:
            return
        depths = trace.get("_depths") or {}
        trace["steps"].append({"index": len(trace["steps"]), "depth": depths.get(id(step), 0),
                               "kind": step.get("_kind"), "alias": step.get("alias"),
                               "summary": _summarise_step(step), "at": self.engine.clock.now().isoformat(),
                               "error": None})

    def list(self, kind: str, item_id: str) -> list[dict[str, Any]]:
        key = (kind, item_id)
        if key in self._traces:
            return [{k: v for k, v in t.items() if k != "_depths"} for t in self._traces[key]]
        if self.store is None:
            return []
        try:
            return list(self.store.get_kv(f"trace:{kind}:{item_id}") or [])
        except Exception:
            return []


# -- the remap -------------------------------------------------------------------------------------

def normalise_remap(raw: Any) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise EditorError("The remap must be an object")
    out: dict[str, Any] = {"entities": {}, "registry_ids": {}, "devices": {}, "drop_actions": [], "only": []}
    for key in ("entities", "registry_ids", "devices"):
        val = raw.get(key) or {}
        if not isinstance(val, dict):
            raise EditorError(f"remap.{key} must map old ids to new ids")
        for old, new in val.items():
            if not isinstance(old, str) or not isinstance(new, str) or not old or not new:
                raise EditorError(f"remap.{key}: {old!r} -> {new!r} is not a pair of ids")
            if key == "entities" and not (_ENTITY_ID.match(old) and _ENTITY_ID.match(new)):
                raise EditorError(f"remap.entities: {old} -> {new} must both be entity ids")
            if key == "registry_ids" and not _ENTITY_ID.match(new) and not _UUID.match(new):
                raise EditorError(f"remap.registry_ids: {old} must map to an entity id")
            out[key][old] = new
    for rule in raw.get("drop_actions") or []:
        if not isinstance(rule, dict) or not isinstance(rule.get("match"), dict) or not rule["match"]:
            raise EditorError("each remap.drop_actions rule needs a non-empty 'match' object")
        out["drop_actions"].append({"match": dict(rule["match"]), "reason": str(rule.get("reason") or "")})
    only = raw.get("only") or []
    if not isinstance(only, list):
        raise EditorError("remap.only must be a list of automation or script ids")
    out["only"] = [str(x) for x in only]
    return out


_ACTION_LISTS = ("actions", "action", "sequence", "then", "else", "default")


def apply_remap(remap: dict[str, Any], kind: str, item_id: str,
                conf: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """``(new config, changes, dropped actions)``. The input config is not modified."""
    changes: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    if not remap or (remap.get("only") and item_id not in remap["only"]) or kind not in ("automation", "script"):
        return conf, changes, dropped
    ents: dict[str, str] = remap.get("entities") or {}
    regs: dict[str, str] = remap.get("registry_ids") or {}
    devs: dict[str, str] = remap.get("devices") or {}
    rules = remap.get("drop_actions") or []
    patterns = [(re.compile(r"(?<![\w.])" + re.escape(old) + r"(?![\w])"), new) for old, new in ents.items()]

    def matches(action: Any) -> dict[str, Any] | None:
        if not isinstance(action, dict):
            return None
        for rule in rules:
            if all(action.get(k) == v for k, v in rule["match"].items()):
                return rule
        return None

    def walk(value: Any, path: str, key: str | None) -> Any:
        if isinstance(value, dict):
            return {k: walk(v, f"{path}.{k}", str(k)) for k, v in value.items()}
        if isinstance(value, list):
            out = []
            for i, v in enumerate(value):
                rule = matches(v) if key in _ACTION_LISTS else None
                if rule is not None:
                    dropped.append({"path": f"{path}[{i}]".lstrip("."),
                                    "reason": rule["reason"] or "dropped by the remap"})
                    continue
                out.append(walk(v, f"{path}[{i}]", key))
            return out
        if not isinstance(value, str):
            return value
        new = value
        if key == "device_id" and value in devs:
            new = devs[value]
        elif key == "entity_id" and value in regs:
            new = regs[value]
        elif value in ents:
            new = ents[value]
        elif "{{" in value or "{%" in value or "," in value:
            for pat, repl in patterns:
                new = pat.sub(repl, new)
        if new != value:
            changes.append({"path": path.lstrip("."), "old": value, "new": new})
        return new

    out = walk(copy.deepcopy(conf), "", None)
    # An action list given as one dict (``action: {...}``) is matched too.
    for key in _ACTION_LISTS:
        if isinstance(out.get(key), dict) and matches(out[key]) is not None:
            dropped.append({"path": key, "reason": matches(out[key])["reason"]})
            out[key] = []
    return out, changes, dropped


# -- the editor --------------------------------------------------------------------------------------

class Editor:
    """One-item-at-a-time changes to the running engine, persisted in the store."""

    def __init__(self, engine: AutomationEngine, store: Any) -> None:
        self.engine = engine
        self.store = store
        self.hub = engine.hub
        self.remap: dict[str, Any] = normalise_remap(self._kv(REMAP_KEY) or {})
        self.tracer = Tracer(engine, store)
        engine.tracer = self.tracer
        engine.config_hook = self._config_hook
        self._lock = asyncio.Lock()

    # -- store access ------------------------------------------------------------
    def _kv(self, key: str, default: Any = None) -> Any:
        if self.store is None:
            return default
        try:
            return self.store.get_kv(key, default)
        except Exception:
            log.exception("store read failed for %s", key)
            return default

    def stored(self, kind: str) -> list[dict[str, Any]]:
        if self.store is None or not hasattr(self.store, "load_automations"):
            return []
        return self.store.load_automations(kind)

    def stored_one(self, kind: str, item_id: str) -> dict[str, Any] | None:
        for doc in self.stored(kind):
            if str(doc.get("id")) == item_id:
                return doc
        return None

    def _config_hook(self, kind: str, item_id: str, conf: dict[str, Any]) -> dict[str, Any]:
        if not self.remap or not any(self.remap.get(k) for k in ("entities", "registry_ids", "devices",
                                                                 "drop_actions")):
            return conf
        return apply_remap(self.remap, kind, item_id, conf)[0]

    # -- views ---------------------------------------------------------------------
    def summary(self, item: Item) -> dict[str, Any]:
        return {
            "kind": item.kind, "id": item.id, "entity_id": item.entity_id, "alias": item.alias,
            "description": item.description, "icon": item.icon,
            "enabled": bool(item.enabled) if item.kind == "automation" else True, "runnable": item.runnable,
            "unsupported_reason": item.unsupported_reason, "warnings": list(item.warnings),
            "last_triggered": _iso(item.last_triggered), "last_error": item.last_error, "mode": item.mode,
            "running": len(item.runs), "entities": named_entities(item.config),
        }

    def scene_summary(self, sid: str) -> dict[str, Any]:
        scene = self.engine.scenes[sid]
        return {"kind": "scene", "id": scene.id, "entity_id": scene.entity_id, "alias": scene.name,
                "description": "", "icon": scene.icon, "enabled": True, "runnable": True,
                "unsupported_reason": None, "warnings": [], "last_triggered": _iso(scene.last_activated),
                "last_error": None, "mode": "single", "running": 0, "entities": list(scene.entities)}

    def items(self) -> list[dict[str, Any]]:
        out = [self.summary(i) for i in self.engine.automations.values()]
        out += [self.summary(i) for i in self.engine.scripts.values()]
        out += [self.scene_summary(s) for s in self.engine.scenes]
        return sorted(out, key=lambda r: (KINDS.index(r["kind"]), str(r["alias"]).lower()))

    def get(self, kind: str, item_id: str) -> dict[str, Any]:
        self._kind(kind)
        conf = self.stored_one(kind, item_id)
        if kind == "scene":
            if item_id not in self.engine.scenes:
                raise EditorError("No such scene", 404)
            summary = self.scene_summary(item_id)
        else:
            item = self._pool(kind).get(item_id)
            if item is None:
                raise EditorError(f"No such {kind}", 404)
            summary = self.summary(item)
            if conf is None:
                conf = dict(item.config)
        conf = self._public_config(kind, item_id, conf or {})
        # A script's YAML is keyed by its id, as in scripts.yaml, so importing it back updates this script.
        shown = {item_id: conf} if kind == "script" else conf
        return {"item": summary, "config": conf, "yaml": yaml.safe_dump(shown, sort_keys=False, allow_unicode=True)}

    @staticmethod
    def _public_config(kind: str, item_id: str, conf: dict[str, Any]) -> dict[str, Any]:
        conf = dict(conf)
        if kind == "script":
            conf.pop("id", None)
        else:
            conf["id"] = item_id
        return conf

    # -- validation ----------------------------------------------------------------
    @staticmethod
    def _kind(kind: str) -> str:
        if kind not in KINDS:
            raise EditorError(f"Unknown kind {kind!r}; expected automation, script or scene", 404)
        return kind

    def _pool(self, kind: str) -> dict[str, Item]:
        return self.engine.automations if kind == "automation" else self.engine.scripts

    def validate(self, kind: str, conf: Any, item_id: str = "__validate__") -> dict[str, Any]:
        """Schema errors refuse a save; a missing entity only loads the item disabled (as HA does)."""
        self._kind(kind)
        errors: list[str] = []
        if not isinstance(conf, dict):
            return {"valid": False, "errors": ["The configuration must be a mapping"], "unsupported_reason": None,
                    "warnings": []}
        if kind == "scene":
            if not str(conf.get("name") or "").strip():
                errors.append("A scene needs a name")
            ents = conf.get("entities")
            if not isinstance(ents, dict) or not ents:
                errors.append("A scene needs at least one entity under 'entities'")
            else:
                errors += [f"{e} is not an entity id" for e in ents if not _ENTITY_ID.match(str(e))]
            return {"valid": not errors, "errors": errors, "unsupported_reason": None, "warnings": []}
        mode = str(conf.get("mode") or "single")
        if mode not in MODES:
            errors.append(f"mode must be one of {', '.join(MODES)}")
        if kind == "automation":
            if not conf.get("triggers", conf.get("trigger")):
                errors.append("An automation needs at least one trigger")
            if not conf.get("actions", conf.get("action")):
                errors.append("An automation needs at least one action")
            if not str(conf.get("alias") or "").strip():
                errors.append("An automation needs a name (alias)")
        elif not conf.get("sequence"):
            errors.append("A script needs a sequence of actions")
        if "max" in conf:
            try:
                if int(conf["max"]) < 1:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append("max must be a positive number")
        if errors:
            return {"valid": False, "errors": errors, "unsupported_reason": None, "warnings": []}
        item = self.engine._build_item(kind, item_id, copy.deepcopy(conf), set())
        reason = item.unsupported_reason
        if reason and reason.startswith("invalid configuration"):
            return {"valid": False, "errors": [reason], "unsupported_reason": None, "warnings": item.warnings}
        return {"valid": True, "errors": [], "unsupported_reason": reason, "warnings": list(item.warnings)}

    # -- writes ----------------------------------------------------------------------
    def new_id(self, kind: str, conf: dict[str, Any]) -> str:
        if kind == "script":
            base = slugify(str(conf.get("alias") or "script")) or "script"
            sid, n = base, 2
            while sid in self.engine.scripts or self.stored_one("script", sid) is not None:
                sid = f"{base}_{n}"
                n += 1
            return sid
        # HA's editor ids are the creation time in milliseconds.
        nid = str(int(_time.time() * 1000))
        while self.stored_one(kind, nid) is not None or nid in self.engine.automations or nid in self.engine.scenes:
            nid = str(int(nid) + 1)
        return nid

    def _register_entity(self, kind: str, item_id: str, conf: dict[str, Any]) -> None:
        """Give a new item a registry entry, so a rename keeps its entity id (HA does the same)."""
        platform = {"automation": "automation", "script": "script", "scene": "homeassistant"}[kind]
        if self.engine._registry_entity((platform, "scene") if kind == "scene" else (platform,), item_id):
            return
        label = item_id if kind == "script" else str(conf.get("alias") or conf.get("name") or item_id)
        base = f"{kind}.{slugify(label)}"
        taken = set(self.hub.entities) | {s.entity_id for s in self.hub.states.all(kind)}
        eid, n = base, 2
        while eid in taken:
            eid = f"{base}_{n}"
            n += 1
        ent = EntityEntry(entity_id=eid, platform=platform, unique_id=item_id)
        self.hub.entities[eid] = ent
        if self.store is not None and hasattr(self.store, "save_entity"):
            self.store.save_entity(ent)

    def _unregister_entity(self, entity_id: str) -> None:
        self.hub.entities.pop(entity_id, None)
        if self.store is not None and hasattr(self.store, "_exec"):
            with contextlib.suppress(Exception):
                self.store._exec("DELETE FROM entities WHERE entity_id=?", (entity_id,))
        self.hub.states.remove(entity_id)

    async def save(self, kind: str, item_id: str | None, conf: Any, *, create: bool = False) -> dict[str, Any]:
        self._kind(kind)
        if not isinstance(conf, dict):
            raise EditorError("The configuration must be a mapping")
        conf = dict(conf)
        async with self._lock:
            if item_id is None:
                item_id = str(conf.get("id") or "") or self.new_id(kind, conf)
            item_id = str(item_id)
            exists = self.stored_one(kind, item_id) is not None or item_id in (
                self.engine.scenes if kind == "scene" else self._pool(kind))
            if create and exists:
                raise EditorError(f"A {kind} with id {item_id} already exists", 409)
            if not create and not exists:
                raise EditorError(f"No such {kind}", 404)
            check = self.validate(kind, conf, item_id)
            if not check["valid"]:
                raise EditorError("; ".join(check["errors"]), 400, check["errors"])
            doc = dict(conf)
            doc["id"] = item_id
            if self.store is not None:
                self.store.save_automation(item_id, kind, doc)
            self._register_entity(kind, item_id, conf)
            await self._rebuild(kind, item_id, doc)
        return self.get(kind, item_id)

    async def delete(self, kind: str, item_id: str) -> None:
        self._kind(kind)
        async with self._lock:
            if kind == "scene":
                scene = self.engine.scenes.pop(item_id, None)
                if scene is None and self.stored_one(kind, item_id) is None:
                    raise EditorError("No such scene", 404)
                if scene is not None:
                    self._unregister_entity(scene.entity_id)
            else:
                pool = self._pool(kind)
                item = pool.pop(item_id, None)
                if item is None and self.stored_one(kind, item_id) is None:
                    raise EditorError(f"No such {kind}", 404)
                if item is not None:
                    await self._retire(item)
                    self._unregister_entity(item.entity_id)
                    if kind == "script":
                        self.hub.services.pop(("script", item_id), None)
                        self.hub.service_schemas.pop(("script", item_id), None)
                        await self._rebuild_callers(item_id)
            if self.store is not None:
                self.store.delete_automation(item_id, kind)

    async def _retire(self, item: Item) -> None:
        for d in item.detach:
            d()
        item.detach.clear()
        await self.engine._cancel_runs(item)

    def _used_ids(self, skip: str | None) -> set[str]:
        used = {i.entity_id for i in [*self.engine.automations.values(), *self.engine.scripts.values()]}
        used |= {s.entity_id for s in self.engine.scenes.values()}
        used |= {s.entity_id for s in self.engine.schedules.values()}
        used.discard(skip or "")
        return used

    async def _rebuild(self, kind: str, item_id: str, doc: dict[str, Any]) -> None:
        eng = self.engine
        if kind == "scene":
            old = eng.scenes.pop(item_id, None)
            used = self._used_ids(old.entity_id if old else None)
            eng._build_scene(dict(doc), 0, used)
            if old is not None:
                eng.scenes[item_id].last_activated = old.last_activated
            eng._publish_scene(eng.scenes[item_id])
            return
        pool = self._pool(kind)
        old = pool.get(item_id)
        if old is not None:
            await self._retire(old)
        conf = {k: v for k, v in doc.items() if not (kind == "script" and k == "id")}
        item = eng._build_item(kind, item_id, conf, self._used_ids(old.entity_id if old else None))
        if old is not None:
            item.last_triggered = item.last_triggered or old.last_triggered
        pool[item_id] = item
        eng._propagate_script_support()
        if kind == "script":
            eng._register_script_services()
            await self._rebuild_callers(item_id)
        eng._publish(item)
        if eng._running and kind == "automation" and item.enabled and item.runnable:
            eng._attach(item)

    async def _rebuild_callers(self, script_id: str) -> None:
        """Callers judged against the old script (disabled because it was) are judged again."""
        for kind in ("script", "automation"):
            for other in list(self._pool(kind).values()):
                if other.id == script_id or script_id not in other.calls_scripts:
                    continue
                doc = self.stored_one(kind, other.id) or dict(other.config, id=other.id)
                await self._rebuild(kind, other.id, doc)

    async def set_enabled(self, item_id: str, enabled: bool) -> dict[str, Any]:
        item = self.engine.automations.get(item_id)
        if item is None:
            raise EditorError("No such automation", 404)
        await self.engine.set_enabled(item, bool(enabled))
        return self.summary(item)

    async def run(self, kind: str, item_id: str, variables: dict[str, Any] | None = None,
                  skip_condition: bool = True) -> None:
        self._kind(kind)
        if kind == "scene":
            scene = self.engine.scenes.get(item_id)
            if scene is None:
                raise EditorError("No such scene", 404)
            await self.hub.call_service("scene", "turn_on", {"entity_id": scene.entity_id})
            return
        item = self._pool(kind).get(item_id)
        if item is None:
            raise EditorError(f"No such {kind}", 404)
        if not item.runnable:
            raise EditorError(f"This {kind} cannot run: {item.unsupported_reason}", 409)
        if kind == "automation":
            await self.engine.trigger(item_id, variables or {}, skip_condition=skip_condition)
        else:
            await self.engine.run_script(item_id, variables or {}, wait=False)

    # -- export / import -------------------------------------------------------------
    def export(self, kind: str, item_id: str | None = None) -> dict[str, Any]:
        self._kind(kind)
        docs = self.stored(kind)
        if not docs:  # an engine loaded without a store (tests, a config dir)
            if kind == "scene":
                docs = [{"id": s.id, "name": s.name, "entities": s.entities} for s in self.engine.scenes.values()]
            else:
                docs = [dict(i.config, id=i.id) for i in self._pool(kind).values()]
        if item_id is not None:
            docs = [d for d in docs if str(d.get("id")) == item_id]
            if not docs:
                raise EditorError(f"No such {kind}", 404)
        if kind == "script":
            data: Any = {str(d["id"]): {k: v for k, v in d.items() if k != "id"} for d in docs}
        else:
            data = docs
        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True) if docs else ""
        name = f"{kind}s.yaml" if item_id is None else f"{kind}_{slugify(item_id)}.yaml"
        return {"yaml": text, "count": len(docs), "filename": name}

    async def import_yaml(self, kind: str, text: str, overwrite: bool = False) -> dict[str, Any]:
        self._kind(kind)
        try:
            data = yaml.safe_load(text or "")
        except yaml.YAMLError as exc:
            raise EditorError(f"That is not valid YAML: {exc}") from exc
        if data is None:
            raise EditorError("Nothing to import")
        pairs: list[tuple[str | None, Any]] = []
        if kind == "script":
            if not isinstance(data, dict):
                raise EditorError("scripts.yaml is a mapping of script id to script")
            if "sequence" in data:  # one script without its key
                pairs.append((None, data))
            else:
                pairs = [(str(k), v) for k, v in data.items()]
        else:
            items = data if isinstance(data, list) else [data]
            pairs = [(str(d.get("id")) if isinstance(d, dict) and d.get("id") else None, d) for d in items]
        report: dict[str, Any] = {"created": [], "updated": [], "skipped": [], "errors": []}
        for item_id, conf in pairs:
            if not isinstance(conf, dict):
                report["errors"].append(f"{item_id or 'an item'}: not a mapping")
                continue
            exists = item_id is not None and (self.stored_one(kind, item_id) is not None or item_id in (
                self.engine.scenes if kind == "scene" else self._pool(kind)))
            if exists and not overwrite:
                report["skipped"].append({"id": item_id, "reason": "already exists"})
                continue
            try:
                res = await self.save(kind, item_id, conf, create=not exists)
            except EditorError as exc:
                report["errors"].append(f"{item_id or conf.get('alias') or conf.get('name') or 'an item'}: {exc}")
                continue
            report["updated" if exists else "created"].append(res["item"]["id"])
        return report

    # -- the remap ----------------------------------------------------------------------
    def remap_report(self, remap: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        remap = self.remap if remap is None else remap
        rows = []
        saved_hook = self.engine.config_hook
        try:
            for kind in ("automation", "script"):
                docs = self.stored(kind) or [dict(i.config, id=i.id) for i in self._pool(kind).values()]
                for doc in docs:
                    item_id = str(doc.get("id"))
                    conf = {k: v for k, v in doc.items() if not (kind == "script" and k == "id")}
                    new, changes, dropped = apply_remap(remap, kind, item_id, conf)
                    if not changes and not dropped:
                        continue
                    self.engine.config_hook = None
                    before = self.engine._build_item(kind, item_id, copy.deepcopy(conf), set())
                    after = self.engine._build_item(kind, item_id, copy.deepcopy(new), set())
                    live = self._pool(kind).get(item_id)
                    rows.append({"kind": kind, "id": item_id, "alias": str(doc.get("alias") or item_id),
                                 "changes": changes, "dropped": dropped,
                                 "reason_before": before.unsupported_reason,
                                 "reason_after": after.unsupported_reason,
                                 "enabled": bool(live.enabled and live.runnable) if live else False})
        finally:
            self.engine.config_hook = saved_hook
        return rows

    async def set_remap(self, raw: Any) -> list[dict[str, Any]]:
        """Store the remap and rebuild what it touches. Items it repairs stay switched off until enabled."""
        remap = normalise_remap(raw)
        old = self.remap
        touched: set[tuple[str, str]] = set()
        for r in (self.remap_report(old), self.remap_report(remap)):
            touched |= {(row["kind"], row["id"]) for row in r}
        if self.store is not None:
            self.store.set_kv(REMAP_KEY, remap)
            for kind, item_id in touched:
                key = f"{kind}:{item_id}"
                saved = self._kv(key) or {}
                if kind == "automation" and "enabled" not in saved:
                    self.store.set_kv(key, {**saved, "enabled": False})
        self.remap = remap
        async with self._lock:
            for kind, item_id in sorted(touched):
                doc = self.stored_one(kind, item_id)
                live = self._pool(kind).get(item_id)
                if doc is None and live is not None:
                    doc = dict(live.config, id=item_id)
                if doc is not None:
                    if kind == "automation" and live is not None and not live.runnable:
                        live.enabled = False
                    await self._rebuild(kind, item_id, doc)
        return self.remap_report()

    # -- schedules (nielsfaber) ------------------------------------------------------------
    def _scheduler_doc(self) -> dict[str, Any]:
        doc = self._kv(SCHEDULER_KEY) or {}
        if isinstance(doc, list):
            doc = {"schedules": doc}
        doc = dict(doc)
        doc.setdefault("schedules", [])
        doc.setdefault("tags", [])
        return doc

    def schedule_conf(self, sid: str) -> dict[str, Any] | None:
        for conf in self._scheduler_doc()["schedules"]:
            if str(conf.get("schedule_id")) == sid:
                return conf
        return None

    def schedule_view(self, s: Schedule) -> dict[str, Any]:
        conf = self.schedule_conf(s.id) or {}
        cur = None
        with contextlib.suppress(Exception):
            hit = self.engine.current_slot(s, self.engine.clock.now()) if s.enabled else None
            cur = hit[0] if hit else None
        return {
            "schedule_id": s.id, "name": s.name, "entity_id": s.entity_id, "enabled": s.enabled,
            "weekdays": list(s.weekdays), "start_date": s.start_date.isoformat() if s.start_date else None,
            "end_date": s.end_date.isoformat() if s.end_date else None, "repeat_type": s.repeat_type,
            "timeslots": copy.deepcopy(s.timeslots), "tags": list(conf.get("tags") or []),
            "next_trigger": _iso(s.next_trigger), "next_slot": s.next_slot, "current_slot": cur,
            "last_run": _iso(s.last_run), "last_error": s.last_error, "unsupported_reason": s.unsupported_reason,
        }

    def schedules(self) -> list[Schedule]:
        return sorted(self.engine.schedules.values(), key=lambda s: s.name.lower())

    def get_schedule(self, sid: str) -> Schedule:
        s = self.engine.schedules.get(sid)
        if s is None:
            raise EditorError("No such schedule", 404)
        return s

    def validate_schedule(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise EditorError("A schedule must be an object")
        errors: list[str] = []
        name = str(raw.get("name") or "").strip()
        if not name:
            errors.append("A schedule needs a name")
        weekdays = raw.get("weekdays") or ["daily"]
        if not isinstance(weekdays, list) or not all(str(w) in SCHEDULE_DAYS for w in weekdays):
            errors.append("weekdays must be mon..sun, daily, workday or weekend")
        repeat = str(raw.get("repeat_type") or "repeat")
        if repeat not in REPEAT_TYPES:
            errors.append("repeat_type must be repeat, pause or single")
        dates: dict[str, date | None] = {}
        for k in ("start_date", "end_date"):
            v = raw.get(k)
            try:
                dates[k] = date.fromisoformat(str(v)) if v else None
            except ValueError:
                errors.append(f"{k} must be YYYY-MM-DD")
                dates[k] = None
        if dates.get("start_date") and dates.get("end_date") and dates["start_date"] > dates["end_date"]:
            errors.append("start_date is after end_date")
        slots_in = raw.get("timeslots")
        if not isinstance(slots_in, list) or not slots_in:
            errors.append("A schedule needs at least one timeslot")
            slots_in = []
        slots = []
        for i, slot in enumerate(slots_in):
            if not isinstance(slot, dict):
                errors.append(f"timeslot {i + 1} is not an object")
                continue
            for key in ("start", "stop"):
                if key == "stop" and slot.get("stop") in (None, ""):
                    continue
                try:
                    self.engine._slot_time_ok(slot.get(key))
                except (ValueError, Unsupported, TypeError) as exc:
                    errors.append(f"timeslot {i + 1} {key}: {exc}")
            acts = []
            for a in slot.get("actions") or []:
                svc = str((a or {}).get("service") or (a or {}).get("action") or "")
                if "." not in svc:
                    errors.append(f"timeslot {i + 1}: every action needs a service like light.turn_on")
                    continue
                act: dict[str, Any] = {"service": svc}
                eid = a.get("entity_id") or (a.get("target") or {}).get("entity_id")
                if eid:
                    act["entity_id"] = eid
                act["service_data"] = dict(a.get("service_data") or a.get("data") or {})
                acts.append(act)
            if not acts:
                errors.append(f"timeslot {i + 1} has no actions")
            conds = []
            for c in slot.get("conditions") or []:
                if not isinstance(c, dict) or not c.get("entity_id") or c.get("match_type", "is") not in (
                        "is", "not", "below", "above"):
                    errors.append(f"timeslot {i + 1}: a condition needs entity_id, match_type and value")
                    continue
                conds.append({"entity_id": c["entity_id"], "attribute": c.get("attribute", "state"),
                              "value": c.get("value"), "match_type": c.get("match_type", "is")})
            slots.append({"start": str(slot.get("start")), "stop": str(slot["stop"]) if slot.get("stop") else None,
                          "conditions": conds, "condition_type": slot.get("condition_type") if conds else None,
                          "track_conditions": bool(slot.get("track_conditions", False)), "actions": acts})
        if errors:
            raise EditorError("; ".join(errors), 400, errors)
        return {"timeslots": slots, "weekdays": [str(w) for w in weekdays],
                "start_date": raw.get("start_date") or None, "end_date": raw.get("end_date") or None,
                "repeat_type": repeat, "name": name, "enabled": bool(raw.get("enabled", True)),
                "tags": [str(t) for t in raw.get("tags") or []]}

    def _scheduler_entry(self) -> ConfigEntry:
        for entry in self.hub.config_entries.values():
            if entry.domain == "scheduler":
                return entry
        entry = ConfigEntry(entry_id=secrets.token_hex(13), domain="scheduler", title="Scheduler", source="user")
        self.hub.config_entries[entry.entry_id] = entry
        if self.store is not None and hasattr(self.store, "save_config_entry"):
            try:
                self.store.save_config_entry(entry)
            except Exception as exc:  # no FERNET_KEY: the entry lives in memory and is made again next time
                log.warning("scheduler config entry not saved: %s", exc)
        self.engine._install_schedule_driver()
        return entry

    def _write_scheduler(self, schedules: list[dict[str, Any]]) -> None:
        doc = self._scheduler_doc()
        doc["schedules"] = schedules
        if self.store is not None:
            self.store.set_kv(SCHEDULER_KEY, doc)

    async def save_schedule(self, sid: str | None, raw: Any) -> Schedule:
        conf = self.validate_schedule(raw)
        async with self._lock:
            stored = self._scheduler_doc()["schedules"]
            if sid is None:
                sid = secrets.token_hex(3)
                while sid in self.engine.schedules or any(str(c.get("schedule_id")) == sid for c in stored):
                    sid = secrets.token_hex(3)
                new = True
            else:
                if sid not in self.engine.schedules and self.schedule_conf(sid) is None:
                    raise EditorError("No such schedule", 404)
                new = False
            conf = {"schedule_id": sid, **conf}
            entry = self._scheduler_entry()
            old = self.engine.schedules.pop(sid, None)
            if old is not None and old.cancel:
                old.cancel()
                old.cancel = None
            if not self.engine._registry_entity(("scheduler",), sid):
                base = f"switch.schedule_{slugify(conf['name']) or sid}"
                taken = set(self.hub.entities)
                eid, n = (old.entity_id, 0) if old is not None else (base, 2)
                while old is None and eid in taken:
                    eid = f"{base}_{n}"
                    n += 1
                ent = EntityEntry(entity_id=eid, platform="scheduler", unique_id=sid,
                                  config_entry_id=entry.entry_id, original_name=conf["name"])
                self.hub.entities[eid] = ent
                if self.store is not None and hasattr(self.store, "save_entity"):
                    self.store.save_entity(ent)
            stored = [c for c in stored if str(c.get("schedule_id")) != sid]
            stored.append(conf)
            self._write_scheduler(stored)
            # The enabled flag lives in the store (switch.turn_on/off writes it); the body wins here.
            if self.store is not None:
                prev = self._kv(f"schedule:{sid}") or {}
                self.store.set_kv(f"schedule:{sid}", {**prev, "enabled": conf["enabled"]})
            self.engine._build_schedules([conf], self._used_ids(old.entity_id if old else None))
            s = self.engine.schedules[sid]
            if old is not None:
                s.last_run = old.last_run
            self.engine._arm_schedule(s)
        # Like the scheduler card: a schedule saved (or switched on) inside a timeslot acts on it now.
        if s.enabled and not s.unsupported_reason and (new or old is not None):
            await self._run_current(s)
        return s

    async def _run_current(self, s: Schedule) -> None:
        if not self.engine._running:
            return
        with contextlib.suppress(Exception):
            cur = self.engine.current_slot(s, self.engine.clock.now())
            if cur is not None:
                await self.engine._run_slot(s, cur[0], cur[1])

    async def delete_schedule(self, sid: str) -> None:
        async with self._lock:
            s = self.engine.schedules.pop(sid, None)
            if s is None and self.schedule_conf(sid) is None:
                raise EditorError("No such schedule", 404)
            if s is not None:
                if s.cancel:
                    s.cancel()
                self._unregister_entity(s.entity_id)
            self._write_scheduler([c for c in self._scheduler_doc()["schedules"]
                                   if str(c.get("schedule_id")) != sid])

    async def set_schedule_enabled(self, sid: str, enabled: bool) -> Schedule:
        s = self.get_schedule(sid)
        was = s.enabled
        await self.engine.set_schedule_enabled(s, bool(enabled))
        stored = self._scheduler_doc()["schedules"]
        for c in stored:
            if str(c.get("schedule_id")) == sid:
                c["enabled"] = bool(enabled)
        self._write_scheduler(stored)
        if enabled and not was:
            await self._run_current(s)
        return s


# -- the catalog ------------------------------------------------------------------------------------

def _f(name: str, label: str, kind: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "label": label, "kind": kind, **extra}


TRIGGER_SPECS = [
    {"type": "device_onoff", "label": "When a device turns on or off",
     "fields": [_f("entity_id", "Device", "entity", required=True), _f("to", "Turns", "select", options=["on", "off"]),
                _f("for", "For at least", "duration")]},
    {"type": "state", "label": "Entity state changes",
     "fields": [_f("entity_id", "Entity", "entities", required=True), _f("attribute", "Attribute", "text"),
                _f("from", "From", "text"), _f("to", "To", "text"), _f("for", "For at least", "duration")]},
    {"type": "numeric_state", "label": "Number goes above or below",
     "fields": [_f("entity_id", "Entity", "entities", required=True), _f("attribute", "Attribute", "text"),
                _f("above", "Above", "number"), _f("below", "Below", "number"), _f("for", "For at least", "duration")]},
    {"type": "time", "label": "At a time", "fields": [_f("at", "Time", "time", required=True),
                                                       _f("weekday", "On days", "weekdays")]},
    {"type": "time_pattern", "label": "Every so often",
     "fields": [_f("hours", "Hours", "text"), _f("minutes", "Minutes (e.g. /10)", "text"),
                _f("seconds", "Seconds", "text")]},
    {"type": "sun", "label": "Sunrise or sunset",
     "fields": [_f("event", "Event", "select", options=["sunrise", "sunset"], required=True),
                _f("offset", "Offset (e.g. -00:30:00)", "duration")]},
    {"type": "zone", "label": "Someone enters or leaves a zone",
     "fields": [_f("entity_id", "Person or tracker", "entities", domain="person", required=True),
                _f("zone", "Zone", "entity", domain="zone", required=True),
                _f("event", "Event", "select", options=["enter", "leave"])]},
    {"type": "template", "label": "Template becomes true",
     "fields": [_f("value_template", "Template", "template", required=True), _f("for", "For at least", "duration")]},
    {"type": "homeassistant", "label": "When the hub starts or stops",
     "fields": [_f("event", "Event", "select", options=["start", "shutdown"], required=True)]},
    {"type": "event", "label": "An event fires",
     "fields": [_f("event_type", "Event type", "text", required=True), _f("event_data", "Event data", "template")]},
]
CONDITION_SPECS = [
    {"type": "state", "label": "Entity is in a state",
     "fields": [_f("entity_id", "Entity", "entities", required=True), _f("attribute", "Attribute", "text"),
                _f("state", "State", "text", required=True), _f("for", "For at least", "duration")]},
    {"type": "numeric_state", "label": "Number is above or below",
     "fields": [_f("entity_id", "Entity", "entities", required=True), _f("attribute", "Attribute", "text"),
                _f("above", "Above", "number"), _f("below", "Below", "number")]},
    {"type": "time", "label": "Time of day",
     "fields": [_f("after", "After", "time"), _f("before", "Before", "time"), _f("weekday", "On days", "weekdays")]},
    {"type": "sun", "label": "Before or after sunrise or sunset",
     "fields": [_f("after", "After", "select", options=["sunrise", "sunset"]),
                _f("before", "Before", "select", options=["sunrise", "sunset"]),
                _f("after_offset", "After offset", "duration"), _f("before_offset", "Before offset", "duration")]},
    {"type": "zone", "label": "Someone is in a zone",
     "fields": [_f("entity_id", "Person or tracker", "entities", domain="person", required=True),
                _f("zone", "Zone", "entity", domain="zone", required=True)]},
    {"type": "template", "label": "Template is true",
     "fields": [_f("value_template", "Template", "template", required=True)]},
    {"type": "and", "label": "All of", "fields": []},
    {"type": "or", "label": "Any of", "fields": []},
    {"type": "not", "label": "None of", "fields": []},
]
ACTION_SPECS = [
    {"type": "service", "label": "Call a service",
     "fields": [_f("action", "Service", "text", required=True), _f("target", "Targets", "entities"),
                _f("data", "Data", "template")]},
    {"type": "delay", "label": "Wait for a time", "fields": [_f("delay", "Duration", "duration", required=True)]},
    {"type": "wait_template", "label": "Wait until a template is true",
     "fields": [_f("wait_template", "Template", "template", required=True), _f("timeout", "Timeout", "duration"),
                _f("continue_on_timeout", "Continue on timeout", "boolean")]},
    {"type": "choose", "label": "Choose an option", "fields": []},
    {"type": "if", "label": "If, then, else", "fields": []},
    {"type": "repeat", "label": "Repeat", "fields": [_f("count", "Times", "number")]},
    {"type": "variables", "label": "Set variables", "fields": [_f("variables", "Variables", "template")]},
    {"type": "stop", "label": "Stop", "fields": [_f("stop", "Reason", "text"), _f("error", "As an error", "boolean")]},
    {"type": "scene", "label": "Activate a scene",
     "fields": [_f("scene", "Scene", "entity", domain="scene", required=True)]},
    {"type": "event", "label": "Fire an event", "fields": [_f("event", "Event type", "text", required=True),
                                                            _f("event_data", "Event data", "template")]},
]


def catalog(hub: Any) -> dict[str, Any]:
    entities = []
    for st in sorted(hub.states.all(), key=lambda s: s.entity_id):
        ent = hub.entities.get(st.entity_id)
        area_id = hub.entity_area_id(st.entity_id) if hasattr(hub, "entity_area_id") else None
        area = hub.areas.get(area_id).name if area_id and area_id in hub.areas else None
        entities.append({"entity_id": st.entity_id, "name": st.attributes.get("friendly_name") or st.entity_id,
                         "domain": st.entity_id.split(".", 1)[0], "area": area,
                         "device_class": st.attributes.get("device_class") or (ent.device_class if ent else None),
                         "unit": st.attributes.get("unit_of_measurement"),
                         # What it can do (capabilities.py), so an editor offers only the services it takes.
                         **{k: v for k, v in capabilities.entity_capabilities(hub, st.entity_id).items()
                            if k in ("abilities", "services")}})
    return {
        "triggers": TRIGGER_SPECS, "conditions": CONDITION_SPECS, "actions": ACTION_SPECS,
        "services": hub.list_services(), "entities": entities,
        "areas": [{"area_id": a.area_id, "name": a.name} for a in sorted(hub.areas.values(), key=lambda a: a.name)],
        "zones": sorted(s.entity_id for s in hub.states.all("zone")),
        "persons": sorted(s.entity_id for s in hub.states.all("person")),
    }


# -- suggesting a remap from HA's recorder -------------------------------------------------------------

#: How long after an old entity stopped reporting a replacement may start and still count as one.
HANDOVER_WINDOW = timedelta(days=4)


def _unresolved(editor: Editor) -> dict[str, Any]:
    """Old entity ids, registry ids and device ids the stored automations name, and what they appear with."""
    hub = editor.hub
    missing_ents: set[str] = set()
    missing_regs: dict[str, dict[str, Any]] = {}
    missing_devs: set[str] = set()
    # old id -> {"areas", "devices"} seen with it alone in a target ("solo"), and with others ("any").
    # A target naming two old thermostats says nothing about which device or area is whose.
    hints: dict[str, dict[str, set[str]]] = {}

    def exists(eid: str) -> bool:
        # Not engine.known_entity: that answers yes to everything when the engine loaded without a registry.
        if eid in ALWAYS_KNOWN or hub.states.get(eid) is not None:
            return True
        ent = hub.entities.get(eid)
        return ent is not None and not ent.disabled_by

    def hint(old: str, key: str, values: list[str]) -> None:
        hints.setdefault(old, {"areas": set(), "devices": set(), "areas_any": set(), "devices_any": set()})[
            key].update(values)

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for x in node:
                visit(x)
            return
        if not isinstance(node, dict):
            return
        scopes = [node] + ([node["target"]] if isinstance(node.get("target"), dict) else [])
        for scope in scopes:
            ents = [e for e in _entity_list(scope.get("entity_id")) if isinstance(e, str)]
            devs = [d for d in _entity_list(scope.get("device_id")) if isinstance(d, str)]
            areas = [a for a in _entity_list(scope.get("area_id")) if isinstance(a, str)]
            old_ents = [e for e in ents if _ENTITY_ID.match(e) and not exists(e)]
            old_regs = [e for e in ents if _UUID.match(e) and e not in (hub.entity_registry_ids or {})]
            old_devs = [d for d in devs if d not in hub.devices]
            missing_ents.update(old_ents)
            missing_devs.update(old_devs)
            for r in old_regs:
                missing_regs.setdefault(r, {"domains": set(), "devices": set()})
                if node.get("domain"):
                    missing_regs[r]["domains"].add(str(node["domain"]))
                missing_regs[r]["devices"].update(devs)
            for e in old_ents:
                solo = len(old_ents) == 1
                hint(e, "areas" if solo else "areas_any", areas)
                hint(e, "devices" if solo else "devices_any", devs)
        for v in node.values():
            visit(v)

    for kind in ("automation", "script"):
        for doc in editor.stored(kind) or [dict(i.config) for i in editor._pool(kind).values()]:
            visit(doc)
    return {"entities": missing_ents, "registry_ids": missing_regs, "devices": missing_devs, "hints": hints}


def _recorder_spans(db_path: str) -> dict[str, dict[str, Any]]:
    """statistic_id -> {"unit", "first", "last_varied"} from HA's long-term statistics."""
    out: dict[str, dict[str, Any]] = {}
    if not db_path or not os.path.exists(db_path):
        return out
    con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    try:
        meta = con.execute("SELECT id, statistic_id, unit_of_measurement FROM statistics_meta").fetchall()
        for mid, sid, unit in meta:
            rows = con.execute("SELECT start_ts, mean, min, max FROM statistics WHERE metadata_id=? AND mean IS NOT NULL "
                               "ORDER BY start_ts", (mid,)).fetchall()
            if not rows:
                continue
            final = rows[-1][1]
            varied = [r[0] for r in rows if (r[2] is not None and r[3] is not None and r[2] != r[3]) or r[1] != final]
            last = max(varied) if varied else rows[-1][0]
            out[sid] = {"unit": unit, "first": datetime.fromtimestamp(rows[0][0], UTC),
                        "last_varied": datetime.fromtimestamp(last, UTC)}
    finally:
        con.close()
    return out


def suggest_remap(editor: Editor, db_path: str | None) -> dict[str, Any]:
    """A proposed remap with the evidence for each pair; an admin confirms before it is saved.

    Old entities are grouped by object id (``x_thermostat`` and ``x_thermostat_indoor_temperature``
    are one old device). A group's replacement is a current device in the same area (from the area the
    automations target alongside it) with an entity of the same domain; with no area, the current entity
    whose recorder history starts within :data:`HANDOVER_WINDOW` of the old one going quiet.
    """
    hub = editor.hub
    found = _unresolved(editor)
    spans = _recorder_spans(db_path or "")
    remap: dict[str, Any] = {"entities": {}, "registry_ids": {}, "devices": {}, "drop_actions": []}
    evidence: list[dict[str, Any]] = []
    olds = sorted(found["entities"])
    # Group: an old id whose object id starts with another old id's object id belongs to it.
    roots = sorted({o.split(".", 1)[1] for o in olds}, key=len)
    groups: dict[str, list[str]] = {}
    for o in olds:
        obj = o.split(".", 1)[1]
        root = next((r for r in roots if obj == r or obj.startswith(r + "_")), obj)
        groups.setdefault(root, []).append(o)

    def device_entities(dev_id: str) -> list[EntityEntry]:
        return [e for e in hub.entities.values() if e.device_id == dev_id and not e.disabled_by]

    def area_of_device(dev_id: str) -> str | None:
        d = hub.devices.get(dev_id)
        return d.area_id if d else None

    def new_device_for(group: list[str]) -> tuple[str | None, str]:
        areas: set[str] = set()
        for o in group:
            areas |= found["hints"].get(o, {}).get("areas", set())
        if not areas:
            for o in group:
                areas |= found["hints"].get(o, {}).get("areas_any", set())
        main_domains = {o.split(".", 1)[0] for o in group if not o.startswith("sensor.")} or {"sensor"}
        candidates = []
        for dev in hub.devices.values():
            if areas and area_of_device(dev.id) not in areas:
                continue
            doms = {e.domain for e in device_entities(dev.id)}
            if main_domains <= doms:
                candidates.append(dev.id)
        quiet = [spans[o]["last_varied"] for o in group if o in spans]
        if len(candidates) > 1 and quiet:
            # Prefer the device whose history starts right after the old one went quiet.
            def gap(dev_id: str) -> float:
                firsts = [spans[e.entity_id]["first"] for e in device_entities(dev_id) if e.entity_id in spans]
                if not firsts:
                    return float("inf")
                return abs((min(firsts) - max(quiet)).total_seconds())
            candidates.sort(key=gap)
            if gap(candidates[0]) > HANDOVER_WINDOW.total_seconds():
                candidates = []
        if len(candidates) == 1 or (candidates and quiet):
            why = f"in area {', '.join(sorted(areas))}" if areas else "only candidate"
            if quiet:
                firsts = [spans[e.entity_id]["first"] for e in device_entities(candidates[0]) if e.entity_id in spans]
                if firsts:
                    why += (f"; the old entity stopped reporting {max(quiet).date()} and this device's history "
                            f"starts {min(firsts).date()}")
            return candidates[0], why
        return None, "no single device in the same area matches" if areas else "no area hint"

    def pick(dev_id: str, old: str) -> str | None:
        domain = old.split(".", 1)[0]
        ents = [e for e in device_entities(dev_id) if e.domain == domain]
        if len(ents) == 1:
            return ents[0].entity_id
        old_unit = spans.get(old, {}).get("unit")
        words = set(old.split(".", 1)[1].split("_"))
        scored = []
        for e in ents:
            unit = spans.get(e.entity_id, {}).get("unit") or e.trusted("unit_of_measurement")
            if old_unit and unit and unit != old_unit:
                continue
            ewords = set(e.entity_id.split(".", 1)[1].split("_"))
            if "outdoor" in ewords and "outdoor" not in words:
                continue
            scored.append((len(words & ewords), e.entity_id))
        scored.sort(reverse=True)
        return scored[0][1] if scored else None

    dev_map: dict[str, str] = {}
    dev_conflicts: set[str] = set()
    for _root, group in sorted(groups.items()):
        dev_id, why = new_device_for(group)
        if dev_id is None:
            for o in group:
                evidence.append({"old": o, "new": None, "kind": "entity", "reason": why})
            continue
        for o in group:
            new = pick(dev_id, o)
            if new:
                remap["entities"][o] = new
            evidence.append({"old": o, "new": new, "kind": "entity",
                             "reason": why if new else "no matching entity on the replacement device"})
            for old_dev in found["hints"].get(o, {}).get("devices", set()):
                if old_dev in hub.devices:
                    continue
                if dev_map.get(old_dev, dev_id) != dev_id:
                    dev_conflicts.add(old_dev)
                dev_map[old_dev] = dev_id
    for old_dev in sorted(dev_conflicts):
        dev_map.pop(old_dev, None)
        evidence.append({"old": old_dev, "new": None, "kind": "device",
                         "reason": "named alone with entities that map to different devices"})
    for old_dev, dev_id in sorted(dev_map.items()):
        remap["devices"][old_dev] = dev_id
        evidence.append({"old": old_dev, "new": dev_id, "kind": "device",
                         "reason": "named together with the remapped entities in the same targets"})
    for reg, info in sorted(found["registry_ids"].items()):
        new_dev = next((dev_map.get(d) or (d if d in hub.devices else None) for d in info["devices"]
                        if dev_map.get(d) or d in hub.devices), None)
        new = None
        if new_dev:
            ents = [e for e in device_entities(new_dev) if e.domain in info["domains"]]
            new = ents[0].entity_id if len(ents) == 1 else None
        if new:
            remap["registry_ids"][reg] = new
        evidence.append({"old": reg, "new": new, "kind": "registry_id",
                         "reason": "the device action's device was remapped" if new else "its device is unknown"})
    # Device actions setting a preset the replacement does not offer would fail the whole run.
    presets_dropped: set[str] = set()
    for kind in ("automation", "script"):
        for doc in editor.stored(kind):
            for node in _iter_dicts(doc):
                if node.get("type") != "set_preset_mode" or not node.get("preset_mode"):
                    continue
                target = remap["registry_ids"].get(str(node.get("entity_id"))) or str(node.get("entity_id"))
                st = hub.states.get(target)
                offered = (st.attributes.get("preset_modes") or []) if st else []
                key = f"{node['preset_mode']}"
                if node["preset_mode"] not in offered and key not in presets_dropped:
                    presets_dropped.add(key)
                    remap["drop_actions"].append({
                        "match": {"type": "set_preset_mode", "preset_mode": node["preset_mode"]},
                        "reason": f"the replacement thermostat has no preset {node['preset_mode']!r}"})
    return {"remap": remap, "evidence": evidence}


def _iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from _iter_dicts(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_dicts(v)
