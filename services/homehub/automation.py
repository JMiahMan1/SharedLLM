"""Automations, scripts, scenes and the scheduler, in place of Home Assistant's.

The household's ``automations.yaml``/``scripts.yaml``/``scenes.yaml`` and the
nielsfaber scheduler's ``scheduler.storage`` load as written, in both HA's old
syntax (``platform:``/``service:``/``trigger:``) and its current one
(``trigger:``/``action:``/``triggers:``/``actions:``). The engine runs on the
hub's EventBus and StateMachine and calls services through
``hub.call_service``, so automations drive the same drivers Jarvis does.

What does not carry over is reported, never guessed at. An automation that
names an entity, a registry id or an integration that no longer exists (HA
Assist timers, ``input_text`` helpers, a deleted thermostat) loads disabled
with an ``unsupported_reason`` instead of failing the load or running half of
itself against nothing.

All timing (time triggers, ``for:``, delays, waits, the scheduler) goes through
one :class:`Scheduler` on an injectable :class:`Clock`. Deadlines are absolute
wall-clock instants and the next daily occurrence is computed from the one that
just fired, never from "now + 24 h", so nothing drifts; the loop never sleeps
longer than ``MAX_SLEEP`` so a clock step is noticed promptly.
"""
from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
import logging
import math
import os
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from services.homehub import templating
from services.homehub.core import (
    UNAVAILABLE,
    UNKNOWN,
    Driver,
    EntityEntry,
    HubError,
    ServiceCall,
    ServiceNotFound,
    State,
)

log = logging.getLogger("homehub.automation")

MAX_SLEEP = 30.0
MODES = ("single", "restart", "queued", "parallel")
DEFAULT_MAX = 10
# Entities that exist without a registry entry.
ALWAYS_KNOWN = {"zone.home", "sun.sun"}
# Helper integrations whose entities HA kept in its own storage. When none of
# their entities came across, a service or event of that domain has nothing to
# act on (the household's Assist timers are the case in point).
HELPER_DOMAINS = {"timer", "input_text", "input_number", "input_boolean", "input_select", "input_datetime",
                  "input_button", "counter"}
TOGGLE_DOMAINS = {"light", "switch", "fan", "input_boolean", "humidifier", "remote", "siren", "media_player",
                  "automation", "script"}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
UNSUPPORTED_TRIGGERS = {"mqtt", "webhook", "tag", "calendar", "conversation", "geo_location",
                        "persistent_notification", "sentence", "update"}


class Unsupported(Exception):
    """Configuration the engine cannot run faithfully; the item loads disabled with this reason."""


class _StopScript(Exception):
    def __init__(self, response: Any = None, error: str | None = None) -> None:
        super().__init__(error or "stopped")
        self.response = response
        self.error = error


class _ConditionFail(Exception):
    """A condition step was false: the enclosing sequence ends, without an error."""


class _WaitTimeout(Exception):
    """A wait timed out with ``continue_on_timeout: false``: the run ends."""


# -- time ---------------------------------------------------------------------------

class Clock:
    """Wall-clock time. Tests substitute :class:`FakeClock`."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def wait(self, timeout: float, wake: asyncio.Event) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(wake.wait(), max(0.0, timeout))


class FakeClock(Clock):
    """A clock that only moves when told to (``set``/``advance``)."""

    def __init__(self, start: datetime) -> None:
        self._now = start.astimezone(UTC)
        self._tick: asyncio.Event | None = None

    def now(self) -> datetime:
        return self._now

    def set(self, when: datetime) -> None:
        self._now = when.astimezone(UTC)
        if self._tick is not None:
            self._tick.set()

    def advance(self, seconds: float) -> None:
        self.set(self._now + timedelta(seconds=seconds))

    async def wait(self, timeout: float, wake: asyncio.Event) -> None:
        if self._tick is None:
            self._tick = asyncio.Event()
        tick = self._tick
        waits = [asyncio.ensure_future(wake.wait()), asyncio.ensure_future(tick.wait())]
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waits:
                w.cancel()
            tick.clear()


@dataclass(order=True)
class _Timer:
    when: datetime
    seq: int
    callback: Callable[[datetime], Any] = field(compare=False)
    cancelled: bool = field(default=False, compare=False)


class Scheduler:
    """Run callbacks at absolute instants. One loop, one heap, no per-timer tasks."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._heap: list[_Timer] = []
        self._seq = itertools.count()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    def at(self, when: datetime, callback: Callable[[datetime], Any]) -> Callable[[], None]:
        """Call ``callback(when)`` at ``when``; returns a cancel function."""
        timer = _Timer(when.astimezone(UTC), next(self._seq), callback)
        heapq.heappush(self._heap, timer)
        self._wake.set()

        def cancel() -> None:
            timer.cancelled = True

        return cancel

    def after(self, seconds: float, callback: Callable[[datetime], Any]) -> Callable[[], None]:
        return self.at(self.clock.now() + timedelta(seconds=seconds), callback)

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        fut: asyncio.Future = asyncio.get_running_loop().create_future()

        def done(_when: datetime) -> None:
            if not fut.done():
                fut.set_result(None)

        cancel = self.after(seconds, done)
        try:
            await fut
        finally:
            cancel()

    def next_due(self) -> datetime | None:
        while self._heap and self._heap[0].cancelled:
            heapq.heappop(self._heap)
        return self._heap[0].when if self._heap else None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        self._heap.clear()

    def run_due(self) -> None:
        now = self.clock.now()
        while self._heap and self._heap[0].when <= now:
            timer = heapq.heappop(self._heap)
            if timer.cancelled:
                continue
            try:
                result = timer.callback(timer.when)
                if asyncio.iscoroutine(result):
                    _spawn(result)
            except Exception:
                log.exception("scheduled callback failed")

    async def _run(self) -> None:
        while True:
            self.run_due()
            nxt = self.next_due()
            timeout = MAX_SLEEP if nxt is None else min(MAX_SLEEP, (nxt - self.clock.now()).total_seconds())
            self._wake.clear()
            await self.clock.wait(timeout, self._wake)


_BACKGROUND: set[asyncio.Task] = set()


def _spawn(coro: Awaitable[Any]) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return task


# -- parsing helpers ---------------------------------------------------------------------

def parse_duration(value: Any) -> timedelta:
    """HA's ``cv.time_period``: seconds, "HH:MM[:SS]", "-HH:MM:SS" or a dict of units."""
    if value is None or value == "":
        return timedelta(0)
    if isinstance(value, timedelta):
        return value
    if isinstance(value, bool):
        raise ValueError(f"not a duration: {value!r}")
    if isinstance(value, (int, float)):
        return timedelta(seconds=value)
    if isinstance(value, dict):
        units = {"days": 0.0, "hours": 0.0, "minutes": 0.0, "seconds": 0.0, "milliseconds": 0.0}
        for k, v in value.items():
            if k not in units:
                raise ValueError(f"unknown duration unit {k!r}")
            units[k] = float(v or 0)
        return timedelta(**units)
    s = str(value).strip()
    neg = s.startswith("-")
    s = s.lstrip("+-")
    if templating.is_number(s):
        td = timedelta(seconds=float(s))
    else:
        parts = s.split(":")
        if not 2 <= len(parts) <= 3:
            raise ValueError(f"not a duration: {value!r}")
        h, m = int(parts[0]), int(parts[1])
        sec = float(parts[2]) if len(parts) == 3 else 0.0
        td = timedelta(hours=h, minutes=m, seconds=sec)
    return -td if neg else td


def parse_time(value: Any) -> time:
    """A time of day. PyYAML reads unquoted ``21:00:00`` as sexagesimal seconds, so ints count too."""
    if isinstance(value, time):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return (datetime.min + timedelta(seconds=value)).time()
    s = str(value).strip()
    parts = s.split(":")
    if not 2 <= len(parts) <= 3 or not all(p.replace(".", "", 1).isdigit() for p in parts):
        raise ValueError(f"not a time: {value!r}")
    sec = float(parts[2]) if len(parts) == 3 else 0.0
    return time(int(parts[0]), int(parts[1]), int(sec), round((sec % 1) * 1e6))


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _entity_list(value: Any) -> list[str]:
    out: list[str] = []
    for v in _as_list(value):
        if isinstance(v, str):
            out.extend(x.strip().lower() for x in v.split(",") if x.strip())
        else:
            out.append(str(v))
    return out


def _state_str(value: Any) -> str | None:
    """YAML 1.1 reads unquoted on/off as booleans; HA state values are strings."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def _is_template(value: Any) -> bool:
    return isinstance(value, str) and templating.is_template_string(value)


def slugify(text: str) -> str:
    return templating.slugify(text) or "unnamed"


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# -- YAML with blueprints ----------------------------------------------------------------

class _Loader(yaml.SafeLoader):
    """Reads HA's tags as markers. ``!input`` gets the same shape the importer stores."""


def _tag(loader: yaml.SafeLoader, node: yaml.Node) -> dict[str, Any]:
    return {"__tag__": node.tag, "value": loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode) else None}


for _t in ("!secret", "!include", "!include_dir_list", "!include_dir_merge_list", "!include_dir_named",
           "!include_dir_merge_named", "!env_var", "!input"):
    _Loader.add_constructor(_t, _tag)


def load_yaml(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return yaml.load(f, Loader=_Loader)


def load_blueprints(config_dir: str) -> dict[str, dict[str, Any]]:
    """``blueprints/<domain>/<path>`` -> parsed blueprint, keyed by the ``use_blueprint`` path."""
    out: dict[str, dict[str, Any]] = {}
    root = os.path.join(config_dir, "blueprints")
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.endswith((".yaml", ".yml")):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).split(os.sep, 1)
            if len(rel) == 2:
                try:
                    out[rel[1].replace(os.sep, "/")] = load_yaml(full)
                except (OSError, yaml.YAMLError):
                    log.warning("blueprint %s could not be read", full)
    return out


def _blueprint_inputs(spec: dict[str, Any]) -> dict[str, Any]:
    """Blueprint input defaults, flattening input sections."""
    out: dict[str, Any] = {}
    for name, conf in (spec or {}).items():
        conf = conf or {}
        if isinstance(conf, dict) and "input" in conf and isinstance(conf["input"], dict):
            out.update(_blueprint_inputs(conf["input"]))
        elif isinstance(conf, dict):
            out[name] = conf.get("default", _MISSING)
    return out


_MISSING = object()


def _substitute_inputs(value: Any, inputs: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if value.get("__tag__") == "!input":
            name = value.get("value")
            if name not in inputs or inputs[name] is _MISSING:
                raise Unsupported(f"blueprint input {name!r} has no value")
            return inputs[name]
        return {k: _substitute_inputs(v, inputs) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_inputs(v, inputs) for v in value]
    return value


def expand_blueprint(conf: dict[str, Any], blueprints: dict[str, dict[str, Any]]) -> dict[str, Any]:
    use = conf.get("use_blueprint") or {}
    path = str(use.get("path") or "")
    bp = blueprints.get(path)
    if bp is None:
        raise Unsupported(f"blueprint {path!r} was not imported")
    inputs = _blueprint_inputs((bp.get("blueprint") or {}).get("input") or {})
    inputs.update(use.get("input") or {})
    body = _substitute_inputs({k: v for k, v in bp.items() if k != "blueprint"}, inputs)
    merged = dict(body)
    for k, v in conf.items():
        if k != "use_blueprint":
            merged[k] = v
    return merged


# -- the items ----------------------------------------------------------------------------

@dataclass
class Item:
    kind: str  # automation | script
    id: str
    alias: str
    entity_id: str
    config: dict[str, Any]
    mode: str = "single"
    max: int = DEFAULT_MAX
    triggers: list[dict[str, Any]] = field(default_factory=list)
    conditions: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)
    trigger_variables: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, Any] = field(default_factory=dict)
    icon: str | None = None
    description: str = ""
    enabled: bool = True
    unsupported_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    last_triggered: datetime | None = None
    last_error: str | None = None
    runs: set[asyncio.Task] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    detach: list[Callable[[], None]] = field(default_factory=list)
    calls_scripts: set[str] = field(default_factory=set)

    @property
    def runnable(self) -> bool:
        return self.unsupported_reason is None


@dataclass
class Scene:
    id: str
    name: str
    entity_id: str
    entities: dict[str, Any]
    icon: str | None = None
    last_activated: datetime | None = None


@dataclass
class Schedule:
    id: str
    name: str
    entity_id: str
    timeslots: list[dict[str, Any]]
    weekdays: list[str]
    start_date: date | None
    end_date: date | None
    repeat_type: str
    enabled: bool
    next_trigger: datetime | None = None
    next_slot: int | None = None
    cancel: Callable[[], None] | None = None
    last_run: datetime | None = None
    unsupported_reason: str | None = None
    last_error: str | None = None


# -- the engine ------------------------------------------------------------------------

class AutomationEngine:
    def __init__(self, hub: Any, store: Any = None, *, clock: Clock | None = None) -> None:
        self.hub = hub
        self.store = store
        self.clock = clock or Clock()
        self.scheduler = Scheduler(self.clock)
        self.automations: dict[str, Item] = {}
        self.scripts: dict[str, Item] = {}
        self.scenes: dict[str, Scene] = {}
        self.schedules: dict[str, Schedule] = {}
        self.blueprints: dict[str, dict[str, Any]] = {}
        self._running = False
        self._source: tuple[list[dict], dict[str, dict], list[dict], Any] = ([], {}, [], None)
        self._time_shutdown: datetime | None = None
        self._unsubs: list[Callable[[], None]] = []
        self._tasks: set[asyncio.Task] = set()
        self._services_registered = False
        self._check_refs = False
        try:
            self.tz = ZoneInfo(getattr(hub, "time_zone", None) or "UTC")
        except Exception:
            self.tz = ZoneInfo("UTC")

    # -- public API ----------------------------------------------------------
    async def load(self, automations: list[dict], scripts: dict[str, dict], scenes: list[dict],
                   schedules: list[dict] | dict | None = None, *,
                   blueprints: dict[str, dict] | None = None) -> None:
        """Parse everything. Items that cannot run load disabled with a reason."""
        was_running = self._running
        if was_running:
            await self._detach_all()
        self._source = (list(automations or []), dict(scripts or {}), list(scenes or []), schedules)
        if blueprints is not None:
            self.blueprints = dict(blueprints)
        elif not self.blueprints and self.store is not None:
            self.blueprints = dict(self._kv("blueprints") or {})
        self._check_refs = bool(getattr(self.hub, "entities", None))
        self.scripts = {}
        self.automations = {}
        self.scenes = {}
        self.schedules = {}
        used_ids: set[str] = set()
        for key, conf in (scripts or {}).items():
            item = self._build_item("script", str(key), dict(conf or {}), used_ids)
            self.scripts[item.id] = item
        for idx, conf in enumerate(automations or []):
            conf = dict(conf or {})
            aid = str(conf.get("id") or f"automation_{idx}")
            item = self._build_item("automation", aid, conf, used_ids)
            self.automations[item.id] = item
        self._propagate_script_support()
        for idx, conf in enumerate(scenes or []):
            self._build_scene(dict(conf or {}), idx, used_ids)
        self._build_schedules(schedules, used_ids)
        self._register_services()
        self._register_script_services()
        self._install_schedule_driver()
        for item in [*self.scripts.values(), *self.automations.values()]:
            self._publish(item)
        for scene in self.scenes.values():
            self._publish_scene(scene)
        if was_running:
            await self._attach_all()
        for s in self.schedules.values():
            self._publish_schedule(s)

    async def load_config_dir(self, config_dir: str) -> None:
        """Read an HA config directory directly (tests and one-off checks)."""
        import json

        def read(name: str) -> Any:
            p = os.path.join(config_dir, name)
            return load_yaml(p) if os.path.exists(p) else None

        sched = None
        p = os.path.join(config_dir, ".storage", "scheduler.storage")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                sched = json.load(f).get("data")
        await self.load(read("automations.yaml") or [], read("scripts.yaml") or {}, read("scenes.yaml") or [],
                        sched, blueprints=load_blueprints(config_dir))

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.scheduler.start()
        await self._attach_all()
        await self._startup_schedules()
        for item in self.automations.values():
            for trig in item.triggers:
                if trig["trigger"] == "homeassistant" and trig.get("event") == "start" and item.enabled:
                    self._fire(item, trig, {"event": "start"})

    async def stop(self) -> None:
        if not self._running:
            return
        for item in self.automations.values():
            for trig in item.triggers:
                if trig["trigger"] == "homeassistant" and trig.get("event") == "shutdown" and item.enabled:
                    self._fire(item, trig, {"event": "shutdown"})
        await asyncio.sleep(0)
        self._running = False
        await self._detach_all()
        tasks = [t for item in [*self.automations.values(), *self.scripts.values()] for t in item.runs]
        tasks += list(self._tasks)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.scheduler.stop()

    async def reload(self) -> None:
        if self.store is not None and hasattr(self.store, "load_automations"):
            autos = self.store.load_automations("automation")
            scripts = {s["id"]: {k: v for k, v in s.items() if k != "id"} for s in self.store.load_automations("script")}
            scenes = self.store.load_automations("scene")
            sched = self._kv("scheduler")
            if autos or scripts or scenes or sched:
                await self.load(autos, scripts, scenes, sched)
                return
        await self.load(*self._source)

    def status(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in [*self.automations.values(), *self.scripts.values()]:
            out.append({
                "kind": item.kind, "id": item.id, "entity_id": item.entity_id, "alias": item.alias,
                "enabled": item.enabled and item.runnable,
                "last_triggered": item.last_triggered.isoformat() if item.last_triggered else None,
                "last_error": item.last_error, "unsupported_reason": item.unsupported_reason,
                "warnings": list(item.warnings), "mode": item.mode, "running": len(item.runs),
            })
        for s in self.schedules.values():
            out.append({
                "kind": "schedule", "id": s.id, "entity_id": s.entity_id, "alias": s.name,
                "enabled": s.enabled and s.unsupported_reason is None,
                "last_triggered": s.last_run.isoformat() if s.last_run else None, "last_error": s.last_error,
                "unsupported_reason": s.unsupported_reason, "warnings": [],
                "next_trigger": s.next_trigger.isoformat() if s.next_trigger else None,
            })
        return out

    async def trigger(self, automation_id: str, variables: dict[str, Any] | None = None,
                      skip_condition: bool = True) -> None:
        item = self.automations[automation_id]
        await self._handle_trigger(item, {"platform": None, "trigger": None, "id": None, "idx": None,
                                          "description": "manual"}, skip_condition=skip_condition,
                                   extra=variables or {})

    async def set_enabled(self, item: Item, enabled: bool, stop_actions: bool = True) -> None:
        if enabled == item.enabled:
            return
        item.enabled = enabled
        if not enabled:
            for d in item.detach:
                d()
            item.detach.clear()
            if stop_actions:
                await self._cancel_runs(item)
        elif self._running and item.runnable:
            self._attach(item)
        self._persist(item)
        self._publish(item)

    async def run_script(self, script_id: str, variables: dict[str, Any] | None = None, *,
                         wait: bool = True) -> Any:
        item = self.scripts[script_id]
        task = self._start_run(item, self._script_variables(item, variables or {}))
        if task is None or not wait:
            return None
        return await task

    # -- building ------------------------------------------------------------
    def _kv(self, key: str) -> Any:
        if self.store is None:
            return None
        try:
            return self.store.get_kv(key)
        except Exception:
            log.exception("store read failed for %s", key)
            return None

    def _persist(self, item: Item) -> None:
        if self.store is None:
            return
        try:
            self.store.set_kv(f"{item.kind}:{item.id}", {
                "enabled": item.enabled,
                "last_triggered": item.last_triggered.isoformat() if item.last_triggered else None,
            })
        except Exception:
            log.exception("store write failed for %s", item.entity_id)

    def _registry_entity(self, platforms: Iterable[str], unique_id: str) -> str | None:
        plats = set(platforms)
        for e in getattr(self.hub, "entities", {}).values():
            if e.platform in plats and str(e.unique_id) == unique_id:
                return e.entity_id
        return None

    def _entity_id_for(self, domain: str, platforms: Iterable[str], unique_id: str, fallback: str,
                       used: set[str]) -> str:
        eid = self._registry_entity(platforms, unique_id)
        if eid is None:
            base = f"{domain}.{slugify(fallback)}"
            eid, n = base, 2
            while eid in used or (eid in getattr(self.hub, "entities", {})):
                eid = f"{base}_{n}"
                n += 1
        used.add(eid)
        return eid

    def _build_item(self, kind: str, item_id: str, conf: dict[str, Any], used: set[str]) -> Item:
        alias = str(conf.get("alias") or item_id)
        if kind == "automation":
            entity_id = self._entity_id_for("automation", ("automation",), item_id, alias, used)
        else:
            entity_id = self._entity_id_for("script", ("script",), item_id, item_id, used)
        item = Item(kind=kind, id=item_id, alias=alias, entity_id=entity_id, config=conf)
        try:
            if "use_blueprint" in conf:
                conf = expand_blueprint(conf, self.blueprints)
                item.config = conf
                item.alias = str(conf.get("alias") or alias)
            self._parse_item(item, conf)
        except Unsupported as exc:
            item.unsupported_reason = str(exc)
        except Exception as exc:
            item.unsupported_reason = f"invalid configuration: {exc}"
        self._restore(item, conf)
        if item.unsupported_reason:
            log.warning("%s %s (%s) disabled: %s", kind, item.id, item.alias, item.unsupported_reason)
        return item

    def _restore(self, item: Item, conf: dict[str, Any]) -> None:
        saved = self._kv(f"{item.kind}:{item.id}") or {}
        prev = self.hub.states.get(item.entity_id)
        if "enabled" in saved:
            item.enabled = bool(saved["enabled"])
        elif "initial_state" in conf:
            item.enabled = bool(conf["initial_state"])
        elif item.kind == "automation" and prev is not None and prev.state == "off":
            item.enabled = False
        last = saved.get("last_triggered") or (prev.attributes.get("last_triggered") if prev else None)
        if last:
            with contextlib.suppress(ValueError):
                item.last_triggered = datetime.fromisoformat(str(last))

    def _parse_item(self, item: Item, conf: dict[str, Any]) -> None:
        mode = str(conf.get("mode") or "single")
        if mode not in MODES:
            raise Unsupported(f"unknown mode {mode!r}")
        item.mode = mode
        item.max = int(conf.get("max") or DEFAULT_MAX)
        item.icon = conf.get("icon")
        item.description = str(conf.get("description") or "")
        item.variables = dict(conf.get("variables") or {})
        item.trigger_variables = dict(conf.get("trigger_variables") or {})
        item.fields = dict(conf.get("fields") or {})
        ctx = _ParseContext(self, item)
        if item.kind == "automation":
            raw_triggers = conf.get("triggers", conf.get("trigger"))
            item.triggers = [ctx.trigger(t, i) for i, t in enumerate(_as_list(raw_triggers))]
            item.triggers = [t for t in item.triggers if t is not None]
            raw_conditions = conf.get("conditions", conf.get("condition"))
            item.conditions = ctx.conditions(raw_conditions)
            raw_actions = conf.get("actions", conf.get("action"))
        else:
            raw_actions = conf.get("sequence")
        item.actions = ctx.sequence(raw_actions)

    def _propagate_script_support(self) -> None:
        """A call to a script that cannot run makes the caller unrunnable too."""
        changed = True
        items = [*self.scripts.values(), *self.automations.values()]
        while changed:
            changed = False
            for item in items:
                if not item.runnable:
                    continue
                by_entity = {i.entity_id.split(".", 1)[1]: i for i in self.scripts.values()}
                for sid in sorted(item.calls_scripts):
                    target = self.scripts.get(sid) or by_entity.get(sid)
                    if target is None:
                        reason = f"calls script.{sid}, which does not exist"
                    elif not target.runnable:
                        reason = f"calls script.{sid}, which is disabled: {target.unsupported_reason}"
                    else:
                        continue
                    if self._check_refs or target is not None:
                        item.unsupported_reason = reason
                        changed = True
                        break

    # -- references -----------------------------------------------------------
    def known_entity(self, entity_id: str) -> bool:
        if not self._check_refs:
            return True
        if entity_id in ALWAYS_KNOWN or self.hub.states.get(entity_id) is not None:
            return True
        ent = self.hub.entities.get(entity_id)
        return ent is not None and not ent.disabled_by

    def helper_missing(self, domain: str) -> bool:
        if not self._check_refs or domain not in HELPER_DOMAINS:
            return False
        prefix = domain + "."
        return not any(eid.startswith(prefix) for eid in self.hub.entities) and not self.hub.states.all(domain)

    def resolve_registry_id(self, value: Any) -> str:
        """Device automations name entities by registry id (a uuid) in newer HA, by entity_id in older."""
        v = str(value or "").strip()
        if "." in v:
            if not self.known_entity(v):
                raise Unsupported(f"entity {v} no longer exists")
            return v
        ids = getattr(self.hub, "entity_registry_ids", None) or {}
        eid = ids.get(v)
        if not eid:
            raise Unsupported(f"entity registry id {v or '(none)'} is not in the entity registry")
        if not self.known_entity(eid):
            raise Unsupported(f"entity {eid} (registry id {v}) no longer exists")
        return eid

    # -- scenes and schedules ---------------------------------------------------
    def _build_scene(self, conf: dict[str, Any], idx: int, used: set[str]) -> None:
        sid = str(conf.get("id") or f"scene_{idx}")
        name = str(conf.get("name") or sid)
        eid = self._entity_id_for("scene", ("homeassistant", "scene"), sid, name, used)
        entities = conf.get("entities") or {}
        scene = Scene(sid, name, eid, dict(entities), conf.get("icon"))
        prev = self.hub.states.get(eid)
        if prev is not None and prev.state not in (UNKNOWN, UNAVAILABLE, ""):
            with contextlib.suppress(ValueError):
                scene.last_activated = datetime.fromisoformat(prev.state)
        self.scenes[sid] = scene

    def _build_schedules(self, raw: Any, used: set[str]) -> None:
        if isinstance(raw, dict):
            items = raw.get("schedules") or []
            ts = raw.get("time_shutdown")
            if ts:
                try:
                    self._time_shutdown = datetime.fromisoformat(str(ts))
                except ValueError:
                    self._time_shutdown = None
        else:
            items = raw or []
        for conf in items:
            sid = str(conf.get("schedule_id") or conf.get("id") or "")
            if not sid:
                continue
            name = str(conf.get("name") or "")
            eid = self._registry_entity(("scheduler",), sid)
            if eid is None:
                eid = f"switch.schedule_{slugify(name) if name and slugify(name) != 'unnamed' else sid}"
            used.add(eid)

            def day(v: Any) -> date | None:
                return date.fromisoformat(str(v)) if v else None

            sched = Schedule(id=sid, name=name or sid, entity_id=eid, timeslots=list(conf.get("timeslots") or []),
                             weekdays=[str(w) for w in (conf.get("weekdays") or ["daily"])],
                             start_date=day(conf.get("start_date")), end_date=day(conf.get("end_date")),
                             repeat_type=str(conf.get("repeat_type") or "repeat"),
                             enabled=bool(conf.get("enabled", True)))
            saved = self._kv(f"schedule:{sid}") or {}
            if "enabled" in saved:
                sched.enabled = bool(saved["enabled"])
            if saved.get("last_run"):
                with contextlib.suppress(ValueError):
                    sched.last_run = datetime.fromisoformat(saved["last_run"])
            try:
                for slot in sched.timeslots:
                    self._slot_time_ok(slot.get("start"))
                    for act in slot.get("actions") or []:
                        svc = str(act.get("service") or act.get("action") or "")
                        if "." not in svc:
                            raise Unsupported(f"timeslot action without a service: {act}")
                        for eid2 in _entity_list(act.get("entity_id") or (act.get("target") or {}).get("entity_id")):
                            if not self.known_entity(eid2):
                                raise Unsupported(f"entity {eid2} no longer exists")
            except Unsupported as exc:
                sched.unsupported_reason = str(exc)
            except ValueError as exc:
                sched.unsupported_reason = f"invalid timeslot: {exc}"
            self.schedules[sid] = sched

    def _slot_time_ok(self, value: Any) -> None:
        if value is None:
            raise ValueError("timeslot without a start")
        if not _SUN_SLOT.match(str(value)):
            parse_time(value)

    # -- attach / detach ---------------------------------------------------------
    async def _attach_all(self) -> None:
        for item in self.automations.values():
            if item.enabled and item.runnable:
                self._attach(item)
        for s in self.schedules.values():
            self._arm_schedule(s)
        if self.schedules:
            # The scheduler re-plans when the workday sensor changes (a holiday turns up).
            def rearm(*_a: Any) -> None:
                for s in self.schedules.values():
                    if {"workday", "weekend"} & set(s.weekdays):
                        self._arm_schedule(s)

            self._unsubs.append(self._sub_state(rearm, ["binary_sensor.workday_sensor"]))

    async def _detach_all(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()
        for item in self.automations.values():
            for d in item.detach:
                d()
            item.detach.clear()
        for s in self.schedules.values():
            if s.cancel:
                s.cancel()
                s.cancel = None

    def _attach(self, item: Item) -> None:
        for d in item.detach:
            d()
        item.detach.clear()
        tvars = self._render_variables(item.trigger_variables, {})
        for trig in item.triggers:
            def cb(data: dict[str, Any], trig: dict[str, Any] = trig, item: Item = item) -> None:
                self._fire(item, trig, data)

            try:
                item.detach.append(self.attach_trigger(trig, cb, tvars))
            except Unsupported as exc:
                item.unsupported_reason = str(exc)
                for d in item.detach:
                    d()
                item.detach.clear()
                self._publish(item)
                return
            except Exception:
                log.exception("trigger of %s could not attach", item.entity_id)

    def _fire(self, item: Item, trig: dict[str, Any], data: dict[str, Any]) -> None:
        if not self._running or not item.enabled or not item.runnable:
            return
        tv = {"platform": trig["trigger"], "trigger": trig["trigger"], "id": trig["id"], "idx": trig["idx"],
              "alias": trig.get("alias"), **data}
        task = _spawn(self._handle_trigger(item, tv))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle_trigger(self, item: Item, trig_vars: dict[str, Any], *, skip_condition: bool = False,
                              extra: dict[str, Any] | None = None) -> None:
        variables: dict[str, Any] = {"this": templating.wrap_state(self.hub, self.hub.states.get(item.entity_id)),
                                     "trigger": trig_vars}
        variables.update(self._render_variables(item.trigger_variables, variables))
        variables.update(extra or {})
        try:
            variables.update(self._render_variables(item.variables, variables))
            if not skip_condition and not self.check_conditions(item.conditions, variables):
                return
        except Exception as exc:
            item.last_error = f"condition or variables failed: {exc}"
            log.warning("%s: %s", item.entity_id, item.last_error)
            self._publish(item)
            return
        item.last_triggered = self.clock.now()
        self._persist(item)
        self._start_run(item, variables)
        self._publish(item)

    # -- runs ----------------------------------------------------------------------
    def _start_run(self, item: Item, variables: dict[str, Any]) -> asyncio.Task | None:
        if item.mode == "single" and item.runs:
            log.warning("%s: already running", item.entity_id)
            return None
        if item.mode == "restart" and item.runs:
            for t in list(item.runs):
                t.cancel()
        if item.mode in ("queued", "parallel") and len(item.runs) >= item.max:
            log.warning("%s: maximum number of runs exceeded (%s)", item.entity_id, item.max)
            return None
        if item.kind == "script":
            item.last_triggered = self.clock.now()
            self._persist(item)
        task = asyncio.ensure_future(self._execute(item, variables))
        item.runs.add(task)

        def done(t: asyncio.Task, item: Item = item) -> None:
            item.runs.discard(t)
            self._publish(item)

        task.add_done_callback(done)
        self._publish(item)
        return task

    async def _cancel_runs(self, item: Item) -> None:
        tasks = list(item.runs)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(self, item: Item, variables: dict[str, Any]) -> Any:
        if item.mode == "queued":
            async with item.lock:
                return await self._execute_inner(item, variables)
        return await self._execute_inner(item, variables)

    async def _execute_inner(self, item: Item, variables: dict[str, Any]) -> Any:
        try:
            await self.run_sequence(item.actions, variables)
        except _StopScript as stop:
            if stop.error:
                item.last_error = stop.error
                log.warning("%s stopped with an error: %s", item.entity_id, stop.error)
            return stop.response
        except (_ConditionFail, _WaitTimeout):
            return None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            item.last_error = str(exc) or type(exc).__name__
            log.warning("%s failed: %s", item.entity_id, item.last_error)
            return None
        item.last_error = None
        return None

    def _script_variables(self, item: Item, data: dict[str, Any]) -> dict[str, Any]:
        variables: dict[str, Any] = {}
        for name, spec in item.fields.items():
            if isinstance(spec, dict) and "default" in spec:
                variables[name] = spec["default"]
        variables.update(data)
        variables["this"] = templating.wrap_state(self.hub, self.hub.states.get(item.entity_id))
        variables.update(self._render_variables(item.variables, variables))
        variables.update(data)
        return variables

    def _render_variables(self, spec: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        scope = dict(base)
        for k, v in (spec or {}).items():
            out[k] = self.render(v, scope)
            scope[k] = out[k]
        return out

    def render(self, value: Any, variables: dict[str, Any]) -> Any:
        return templating.render_complex(self.hub, value, variables, now=self.clock.now)

    # -- actions ---------------------------------------------------------------------
    async def run_sequence(self, steps: list[dict[str, Any]], variables: dict[str, Any]) -> None:
        for step in steps:
            if step.get("enabled") is False:
                continue
            try:
                await self._run_step(step, variables)
            except (_StopScript, _ConditionFail, _WaitTimeout, asyncio.CancelledError):
                raise
            except Exception as exc:
                if step.get("continue_on_error"):
                    log.info("continuing past a failed step: %s", exc)
                    continue
                raise

    async def _nested(self, steps: list[dict[str, Any]], variables: dict[str, Any]) -> None:
        """A nested sequence: a false condition step ends it, not the whole run."""
        with contextlib.suppress(_ConditionFail):
            await self.run_sequence(steps, variables)

    async def _run_step(self, step: dict[str, Any], v: dict[str, Any]) -> None:
        kind = step["_kind"]
        if kind == "service":
            await self._call_service(step, v)
        elif kind == "delay":
            await self.scheduler.sleep(parse_duration(self.render(step["delay"], v)).total_seconds())
        elif kind == "wait_template":
            await self._wait_template(step, v)
        elif kind == "wait_for_trigger":
            await self._wait_for_trigger(step, v)
        elif kind == "condition":
            if not self.check_condition(step["condition"], v):
                raise _ConditionFail()
        elif kind == "variables":
            v.update(self._render_variables(step["variables"], v))
        elif kind == "choose":
            for opt in step["choose"]:
                if self.check_conditions(opt["conditions"], v):
                    await self._nested(opt["sequence"], v)
                    return
            if step["default"]:
                await self._nested(step["default"], v)
        elif kind == "if":
            if self.check_conditions(step["if"], v):
                await self._nested(step["then"], v)
            elif step["else"]:
                await self._nested(step["else"], v)
        elif kind == "repeat":
            await self._repeat(step, v)
        elif kind == "sequence":
            await self._nested(step["sequence"], v)
        elif kind == "parallel":
            branches = [self._nested(seq, dict(v)) for seq in step["parallel"]]
            results = await asyncio.gather(*branches, return_exceptions=True)
            for r in results:
                if isinstance(r, BaseException):
                    raise r
        elif kind == "event":
            data = self.render(step.get("event_data") or {}, v)
            data.update(self.render(step.get("event_data_template") or {}, v))
            self.hub.bus.fire(str(self.render(step["event"], v)), data)
        elif kind == "stop":
            response = v.get(step["response_variable"]) if step.get("response_variable") else None
            raise _StopScript(response, str(step["stop"]) if step.get("error") else None)
        elif kind == "scene":
            await self.hub.call_service("scene", "turn_on", {"entity_id": self.render(step["scene"], v)})
        else:  # pragma: no cover - the parser only produces the kinds above
            raise HubError(f"unknown step {kind}")

    async def _call_service(self, step: dict[str, Any], v: dict[str, Any]) -> None:
        action = str(self.render(step["action"], v)).strip()
        if "." not in action:
            raise ServiceNotFound(f"invalid service {action!r}")
        domain, service = action.split(".", 1)
        data = dict(self.render(step.get("data") or {}, v) or {})
        data.update(self.render(step.get("data_template") or {}, v) or {})
        target = self.render(step.get("target") or {}, v) or {}
        if isinstance(target, dict):
            for k in ("entity_id", "area_id", "device_id", "floor_id", "label_id"):
                if k in target and target[k] not in (None, "", []):
                    data[k] = target[k]
        if step.get("entity_id") is not None:
            data["entity_id"] = self.render(step["entity_id"], v)
        if isinstance(data.get("entity_id"), str):
            data["entity_id"] = data["entity_id"].strip()
        rv = step.get("response_variable")
        result = await self.hub.call_service(domain, service, data, return_response=bool(rv))
        if rv:
            v[rv] = (result or {}).get("response")

    async def _repeat(self, step: dict[str, Any], v: dict[str, Any]) -> None:
        rep = step["repeat"]
        seq = rep["sequence"]

        async def iteration(index: int, item: Any = None, last: bool | None = None) -> None:
            info: dict[str, Any] = {"first": index == 1, "index": index}
            if last is not None:
                info["last"] = last
            if item is not None:
                info["item"] = item
            v["repeat"] = info
            await self._nested(seq, v)

        outer = v.get("repeat")
        try:
            if "count" in rep:
                count = int(float(self.render(rep["count"], v)))
                for i in range(1, count + 1):
                    await iteration(i, last=i == count)
            elif "for_each" in rep:
                items = self.render(rep["for_each"], v)
                if isinstance(items, str):
                    items = templating.parse_result(items) if items.strip().startswith("[") else [items]
                items = list(items or [])
                for i, it in enumerate(items, 1):
                    await iteration(i, it, last=i == len(items))
            elif "while" in rep:
                i = 0
                while True:
                    i += 1
                    v["repeat"] = {"first": i == 1, "index": i}
                    if not self.check_conditions(rep["while"], v):
                        break
                    await iteration(i)
                    if i >= 10000:
                        raise HubError("repeat while ran 10000 times; stopping")
            elif "until" in rep:
                i = 0
                while True:
                    i += 1
                    await iteration(i)
                    if self.check_conditions(rep["until"], v):
                        break
                    if i >= 10000:
                        raise HubError("repeat until ran 10000 times; stopping")
        finally:
            if outer is None:
                v.pop("repeat", None)
            else:
                v["repeat"] = outer

    def _wait_result(self, step: dict[str, Any], v: dict[str, Any]) -> tuple[float | None, bool]:
        timeout = step.get("timeout")
        secs = parse_duration(self.render(timeout, v)).total_seconds() if timeout is not None else None
        return secs, bool(step.get("continue_on_timeout", True))

    async def _wait_until(self, check: Callable[[], Any], attach: Callable[[Callable[[Any], None]], Callable[[], None]],
                          timeout: float | None) -> tuple[bool, Any, float | None]:
        """Wait for ``attach``'s callback; returns (completed, value, remaining seconds)."""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        start = self.clock.now()

        def done(value: Any) -> None:
            if not fut.done():
                fut.set_result(value)

        first = check()
        if first is not None:
            return True, first, timeout
        unsub = attach(done)
        cancel_timer = None
        if timeout is not None:
            cancel_timer = self.scheduler.after(timeout, lambda _w: (not fut.done()) and fut.set_result(_TIMEOUT))
        try:
            value = await fut
        finally:
            unsub()
            if cancel_timer:
                cancel_timer()
        elapsed = (self.clock.now() - start).total_seconds()
        remaining = None if timeout is None else max(0.0, timeout - elapsed)
        if value is _TIMEOUT:
            return False, None, 0.0
        return True, value, remaining

    async def _wait_template(self, step: dict[str, Any], v: dict[str, Any]) -> None:
        tpl = step["wait_template"]
        timeout, cont = self._wait_result(step, v)
        if tpl is None or str(tpl).strip() == "":
            # An empty wait has nothing to wait for. HA would block the run forever
            # (the household has one, in "Winter - Set temp to 85"); a no-op is the
            # only useful reading.
            v["wait"] = {"completed": True, "remaining": timeout}
            return

        def check() -> Any:
            return True if templating.render_bool(self.hub, tpl, v, now=self.clock.now) else None

        def attach(done: Callable[[Any], None]) -> Callable[[], None]:
            def on_change(_t: str, _ev: dict[str, Any]) -> None:
                try:
                    if check():
                        done(True)
                except Exception:
                    log.exception("wait_template failed")

            unsubs = [self.hub.bus.subscribe(on_change, "state_changed")]
            if templating.references_now(tpl):
                unsubs.append(self._every_minute(lambda: on_change("", {})))
            return lambda: [u() for u in unsubs]

        completed, _val, remaining = await self._wait_until(check, attach, timeout)
        v["wait"] = {"completed": completed, "remaining": remaining}
        if not completed and not cont:
            raise _WaitTimeout()

    async def _wait_for_trigger(self, step: dict[str, Any], v: dict[str, Any]) -> None:
        timeout, cont = self._wait_result(step, v)

        def attach(done: Callable[[Any], None]) -> Callable[[], None]:
            unsubs = []
            for trig in step["wait_for_trigger"]:
                def cb(data: dict[str, Any], trig: dict[str, Any] = trig) -> None:
                    done({"platform": trig["trigger"], "trigger": trig["trigger"], "id": trig["id"],
                          "idx": trig["idx"], **data})
                unsubs.append(self.attach_trigger(trig, cb, v))
            return lambda: [u() for u in unsubs]

        completed, trig_vars, remaining = await self._wait_until(lambda: None, attach, timeout)
        v["wait"] = {"trigger": trig_vars if completed else None, "remaining": remaining}
        if not completed and not cont:
            raise _WaitTimeout()

    def _every_minute(self, fn: Callable[[], None]) -> Callable[[], None]:
        state: dict[str, Any] = {"cancel": None, "stopped": False}

        def schedule(after: datetime) -> None:
            nxt = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)

            def tick(when: datetime) -> None:
                if state["stopped"]:
                    return
                fn()
                schedule(when)

            state["cancel"] = self.scheduler.at(nxt, tick)

        schedule(self.clock.now())

        def stop() -> None:
            state["stopped"] = True
            if state["cancel"]:
                state["cancel"]()

        return stop

    # -- conditions ------------------------------------------------------------------------
    def check_conditions(self, conditions: list[dict[str, Any]], v: dict[str, Any]) -> bool:
        return all(self.check_condition(c, v) for c in conditions)

    def check_condition(self, c: dict[str, Any], v: dict[str, Any]) -> bool:
        if c.get("enabled") is False:
            return True
        kind = c["condition"]
        if kind == "and":
            return all(self.check_condition(x, v) for x in c["conditions"])
        if kind == "or":
            return any(self.check_condition(x, v) for x in c["conditions"])
        if kind == "not":
            return not any(self.check_condition(x, v) for x in c["conditions"])
        if kind == "template":
            return templating.render_bool(self.hub, c["value_template"], v, now=self.clock.now)
        if kind == "state":
            return self._cond_state(c, v)
        if kind == "numeric_state":
            ids = _entity_list(self.render(c["entity_id"], v))
            return all(self._numeric_match(self.hub.states.get(e), c, v) for e in ids)
        if kind == "time":
            return self._cond_time(c, v)
        if kind == "sun":
            return self._cond_sun(c)
        if kind == "zone":
            ids = _entity_list(self.render(c["entity_id"], v))
            zones = _entity_list(self.render(c["zone"], v))
            return all(any(self._in_zone(self.hub.states.get(e), z) for z in zones) for e in ids)
        if kind == "trigger":
            tid = (v.get("trigger") or {}).get("id")
            return tid is not None and str(tid) in [str(x) for x in _as_list(c["id"])]
        raise HubError(f"unknown condition {kind}")  # pragma: no cover

    def _cond_state(self, c: dict[str, Any], v: dict[str, Any]) -> bool:
        ids = _entity_list(self.render(c["entity_id"], v))
        wanted = [_state_str(x) for x in _as_list(self.render(c["state"], v))]
        attr = c.get("attribute")
        for_td = parse_duration(self.render(c["for"], v)) if c.get("for") is not None else None
        results = []
        for eid in ids:
            st = self.hub.states.get(eid)
            if st is None:
                results.append(False)
                continue
            # A wanted value naming an entity (input_select etc.) compares against that entity's state.
            resolved = []
            for w in wanted:
                ref = self.hub.states.get(w) if w and "." in w and w.split(".", 1)[0].startswith("input_") else None
                resolved.append(ref.state if ref is not None else w)
            value = _state_str(st.attributes.get(attr)) if attr else st.state
            ok = value in resolved
            if ok and for_td is not None:
                ok = self.clock.now() - st.last_changed >= for_td
            results.append(ok)
        return any(results) if c.get("match") == "any" else all(results)

    def _threshold(self, value: Any, v: dict[str, Any]) -> float | None:
        if value is None:
            return None
        value = self.render(value, v)
        if isinstance(value, str) and "." in value and not templating.is_number(value):
            st = self.hub.states.get(value)
            if st is None or not templating.is_number(st.state):
                raise HubError(f"threshold entity {value} has no numeric state")
            return float(st.state)
        return float(value)

    def _numeric_value(self, st: State | None, c: dict[str, Any], v: dict[str, Any]) -> float | None:
        if st is None:
            return None
        if c.get("value_template"):
            raw = templating.render_complex(self.hub, c["value_template"], {**v, "state": templating.wrap_state(
                self.hub, st)}, now=self.clock.now)
        elif c.get("attribute"):
            raw = st.attributes.get(c["attribute"])
        else:
            raw = st.state
        if raw is None or (isinstance(raw, str) and raw in (UNKNOWN, UNAVAILABLE, "")):
            return None
        return float(raw) if templating.is_number(raw) else None

    def _numeric_match(self, st: State | None, c: dict[str, Any], v: dict[str, Any]) -> bool:
        try:
            value = self._numeric_value(st, c, v)
            if value is None:
                return False
            above = self._threshold(c.get("above"), v)
            below = self._threshold(c.get("below"), v)
        except (HubError, ValueError, templating.TemplateError) as exc:
            log.debug("numeric_state check failed: %s", exc)
            return False
        if above is not None and not value > above:
            return False
        return not (below is not None and not value < below)

    def _time_value(self, value: Any, now_local: datetime) -> time | None:
        """A time-of-day from a literal or from an input_datetime / timestamp sensor."""
        if isinstance(value, str) and "." in value and ":" not in value:
            st = self.hub.states.get(value)
            if st is None or st.state in (UNKNOWN, UNAVAILABLE):
                return None
            if st.domain == "input_datetime":
                if not st.attributes.get("has_time", True):
                    return time(0, 0)
                return parse_time(st.state.split(" ")[-1])
            try:
                return datetime.fromisoformat(st.state).astimezone(self.tz).time()
            except ValueError:
                return None
        return parse_time(value)

    def _cond_time(self, c: dict[str, Any], v: dict[str, Any]) -> bool:
        now = self.clock.now().astimezone(self.tz)
        days = [str(d).lower()[:3] for d in _as_list(c.get("weekday"))]
        if days and WEEKDAYS[now.weekday()] not in days:
            return False
        after = self._time_value(c["after"], now) if c.get("after") is not None else time(0, 0)
        before = self._time_value(c["before"], now) if c.get("before") is not None else time(23, 59, 59, 999999)
        if after is None or before is None:
            return False
        t = now.time().replace(tzinfo=None)
        if after < before:
            return after <= t < before or (c.get("before") is None and t >= after)
        return not (before <= t < after)

    def _sun_times(self, day: date) -> dict[str, datetime | None]:
        from astral import Observer
        from astral import sun as astral_sun

        loc = getattr(self.hub, "location", None) or {}
        if "latitude" not in loc or "longitude" not in loc:
            raise Unsupported("the hub has no location, so sun events are unknown")
        obs = Observer(float(loc["latitude"]), float(loc["longitude"]), float(loc.get("elevation") or 0))
        out: dict[str, datetime | None] = {}
        for ev, fn in (("sunrise", astral_sun.sunrise), ("sunset", astral_sun.sunset)):
            try:
                out[ev] = fn(obs, day, tzinfo=self.tz).astimezone(UTC)
            except ValueError:
                out[ev] = None
        return out

    def _cond_sun(self, c: dict[str, Any]) -> bool:
        now = self.clock.now()
        today = now.astimezone(self.tz).date()
        times = self._sun_times(today)
        if c.get("before"):
            ev = times[c["before"]]
            if ev is None or not now < ev + parse_duration(c.get("before_offset")):
                return False
        if c.get("after"):
            ev = times[c["after"]]
            if ev is None or not now > ev + parse_duration(c.get("after_offset")):
                return False
        return True

    def _in_zone(self, st: State | None, zone_id: str) -> bool:
        """HA's in_zone from GPS, falling back to the state naming the zone (trackers without coordinates)."""
        if st is None:
            return False
        zone = self.hub.states.get(zone_id)
        lat, lon = st.attributes.get("latitude"), st.attributes.get("longitude")
        if zone is not None and lat is not None and lon is not None and "latitude" in zone.attributes:
            dist = _distance_m(float(lat), float(lon), float(zone.attributes["latitude"]),
                               float(zone.attributes["longitude"]))
            acc = float(st.attributes.get("gps_accuracy") or 0)
            return dist - acc < float(zone.attributes.get("radius") or 0)
        names = {zone_id.split(".", 1)[1]}
        if zone is not None and zone.attributes.get("friendly_name"):
            names.add(str(zone.attributes["friendly_name"]))
        if zone_id == "zone.home":
            names.add("home")
        return st.state in names

    # -- triggers ---------------------------------------------------------------------------
    def attach_trigger(self, t: dict[str, Any], cb: Callable[[dict[str, Any]], None],
                       variables: dict[str, Any]) -> Callable[[], None]:
        kind = t["trigger"]
        handler = getattr(self, f"_trig_{kind}", None)
        if handler is None:
            raise Unsupported(f"trigger {kind!r} is not supported")
        return handler(t, cb, variables)

    def _sub_state(self, fn: Callable[[str, Any, Any], None], entity_ids: list[str] | None = None) -> Callable[[], None]:
        wanted = set(entity_ids) if entity_ids is not None else None

        def on_change(_t: str, ev: dict[str, Any]) -> None:
            eid = ev.get("entity_id")
            if wanted is not None and eid not in wanted:
                return
            try:
                fn(eid, ev.get("old_state"), ev.get("new_state"))
            except Exception:
                log.exception("trigger evaluation failed for %s", eid)

        return self.hub.bus.subscribe(on_change, "state_changed")

    def _trig_state(self, t: dict[str, Any], cb: Callable, variables: dict[str, Any]) -> Callable[[], None]:
        ids = _entity_list(t["entity_id"])
        attr = t.get("attribute")
        has_from, has_to = "from" in t, "to" in t
        from_v = [_state_str(x) for x in _as_list(t.get("from"))] if t.get("from") is not None else None
        to_v = [_state_str(x) for x in _as_list(t.get("to"))] if t.get("to") is not None else None
        not_from = [_state_str(x) for x in _as_list(t.get("not_from"))]
        not_to = [_state_str(x) for x in _as_list(t.get("not_to"))]
        any_filter = has_from or has_to or "not_from" in t or "not_to" in t
        pending: dict[str, Callable[[], None]] = {}

        def value(st: State | None) -> Any:
            if st is None:
                return None
            return _state_str(st.attributes.get(attr)) if attr else st.state

        def on_change(eid: str, old: State | None, new: State | None) -> None:
            ov, nv = value(old), value(new)
            if eid in pending:
                # HA's "same state" check for ``for:``: from-only triggers wait while the
                # value stays away from where it was; the rest while it stays where it went.
                if new is None:
                    still = False
                elif has_from and not has_to:
                    still = nv != pending_old[eid]
                else:
                    still = nv == pending_new[eid]
                if not still:
                    pending.pop(eid)()
            if new is None:
                return
            if attr is None:
                if old is not None and old.state == new.state and (any_filter or old.attributes == new.attributes):
                    return  # an attribute-only change does not fire a from/to trigger
            elif ov == nv:
                return
            if from_v is not None and ov not in from_v:
                return
            if to_v is not None and nv not in to_v:
                return
            if ov in not_from or nv in not_to:
                return
            data = {"entity_id": eid, "from_state": templating.wrap_state(self.hub, old),
                    "to_state": templating.wrap_state(self.hub, new), "attribute": attr, "for": None,
                    "description": f"state of {eid}"}
            if t.get("for") is None:
                cb(data)
                return
            td = parse_duration(self.render(t["for"], {**variables, "trigger": data}))
            data["for"] = td
            if eid in pending:
                pending.pop(eid)()
            pending_old[eid], pending_new[eid] = ov, nv

            def fire(_w: datetime, eid: str = eid, data: dict[str, Any] = data) -> None:
                pending.pop(eid, None)
                cb(data)

            pending[eid] = self.scheduler.after(td.total_seconds(), fire)

        pending_old: dict[str, Any] = {}
        pending_new: dict[str, Any] = {}
        unsub = self._sub_state(on_change, ids)

        def detach() -> None:
            unsub()
            for c in pending.values():
                c()
            pending.clear()

        return detach

    def _trig_numeric_state(self, t: dict[str, Any], cb: Callable, variables: dict[str, Any]) -> Callable[[], None]:
        ids = _entity_list(t["entity_id"])
        armed = {e for e in ids if not self._numeric_match(self.hub.states.get(e), t, variables)}
        pending: dict[str, Callable[[], None]] = {}

        def on_change(eid: str, old: State | None, new: State | None) -> None:
            matched = self._numeric_match(new, t, variables)
            if not matched:
                armed.add(eid)
                if eid in pending:
                    pending.pop(eid)()
                return
            if eid not in armed:
                return
            armed.discard(eid)
            data = {"entity_id": eid, "from_state": templating.wrap_state(self.hub, old),
                    "to_state": templating.wrap_state(self.hub, new), "above": t.get("above"),
                    "below": t.get("below"), "attribute": t.get("attribute"), "for": None,
                    "description": f"numeric state of {eid}"}
            if t.get("for") is None:
                cb(data)
                return
            td = parse_duration(self.render(t["for"], {**variables, "trigger": data}))
            data["for"] = td

            def fire(_w: datetime, eid: str = eid, data: dict[str, Any] = data) -> None:
                pending.pop(eid, None)
                cb(data)

            pending[eid] = self.scheduler.after(td.total_seconds(), fire)

        unsub = self._sub_state(on_change, ids)

        def detach() -> None:
            unsub()
            for c in pending.values():
                c()
            pending.clear()

        return detach

    def _trig_zone(self, t: dict[str, Any], cb: Callable, variables: dict[str, Any]) -> Callable[[], None]:
        ids = _entity_list(t["entity_id"])
        zone = str(t["zone"])
        event = t.get("event", "enter")

        def on_change(eid: str, old: State | None, new: State | None) -> None:
            if old is None or new is None:
                return
            was, now_in = self._in_zone(old, zone), self._in_zone(new, zone)
            if (event == "enter" and not was and now_in) or (event == "leave" and was and not now_in):
                cb({"entity_id": eid, "from_state": templating.wrap_state(self.hub, old),
                    "to_state": templating.wrap_state(self.hub, new),
                    "zone": templating.wrap_state(self.hub, self.hub.states.get(zone)), "event": event,
                    "description": f"{eid} {event} {zone}"})

        return self._sub_state(on_change, ids)

    def _trig_template(self, t: dict[str, Any], cb: Callable, variables: dict[str, Any]) -> Callable[[], None]:
        tpl = t["value_template"]
        state = {"last": False, "pending": None}

        def evaluate() -> bool:
            try:
                return templating.render_bool(self.hub, tpl, variables, now=self.clock.now)
            except Exception as exc:
                log.debug("template trigger failed: %s", exc)
                return False

        state["last"] = evaluate()

        def check(eid: str | None, old: State | None, new: State | None) -> None:
            res = evaluate()
            if not res:
                if state["pending"]:
                    state["pending"]()
                    state["pending"] = None
                state["last"] = False
                return
            if state["last"]:
                return
            state["last"] = True
            data = {"entity_id": eid, "from_state": templating.wrap_state(self.hub, old),
                    "to_state": templating.wrap_state(self.hub, new), "for": None, "description": "template"}
            if t.get("for") is None:
                cb(data)
                return
            td = parse_duration(self.render(t["for"], {**variables, "trigger": data}))
            data["for"] = td

            def fire(_w: datetime) -> None:
                state["pending"] = None
                cb(data)

            state["pending"] = self.scheduler.after(td.total_seconds(), fire)

        unsubs = [self._sub_state(check)]
        if templating.references_now(tpl):
            unsubs.append(self._every_minute(lambda: check(None, None, None)))

        def detach() -> None:
            for u in unsubs:
                u()
            if state["pending"]:
                state["pending"]()

        return detach

    def _trig_event(self, t: dict[str, Any], cb: Callable, variables: dict[str, Any]) -> Callable[[], None]:
        types = [str(x) for x in _as_list(self.render(t["event_type"], variables))]
        want = self.render(t.get("event_data") or {}, variables)

        def matches(data: Any, spec: Any) -> bool:
            if isinstance(spec, dict):
                return isinstance(data, dict) and all(k in data and matches(data[k], sv) for k, sv in spec.items())
            return data == spec or str(data) == str(spec)

        unsubs = []
        for et in types:
            def on_event(event_type: str, data: dict[str, Any]) -> None:
                if want and not matches(data, want):
                    return
                cb({"event": {"event_type": event_type, "data": data, "origin": "LOCAL",
                              "time_fired": self.clock.now().isoformat()},
                    "description": f"event '{event_type}'"})
            unsubs.append(self.hub.bus.subscribe(on_event, None if et == "*" else et))
        return lambda: [u() for u in unsubs]

    def _trig_homeassistant(self, t: dict[str, Any], cb: Callable, variables: dict[str, Any]) -> Callable[[], None]:
        return lambda: None  # fired by start() and stop()

    def _schedule_series(self, first_after: datetime, nxt: Callable[[datetime], datetime | None],
                         fire: Callable[[datetime], None]) -> Callable[[], None]:
        """Fire at ``nxt(after)``, then at ``nxt(that instant)``: anchored, never drifting."""
        holder: dict[str, Any] = {"cancel": None, "stopped": False}

        def arm(after: datetime) -> None:
            when = nxt(after)
            if when is None or holder["stopped"]:
                return

            def run(at: datetime) -> None:
                if holder["stopped"]:
                    return
                fire(at)
                arm(at)

            holder["cancel"] = self.scheduler.at(when, run)

        arm(first_after)

        def cancel() -> None:
            holder["stopped"] = True
            if holder["cancel"]:
                holder["cancel"]()

        return cancel

    def next_local_time(self, after: datetime, at: time, weekdays: list[str] | None = None) -> datetime:
        local = after.astimezone(self.tz)
        for day in range(0, 9):
            d = local.date() + timedelta(days=day)
            cand = datetime.combine(d, at.replace(tzinfo=None), tzinfo=self.tz)
            if cand > local and (not weekdays or WEEKDAYS[cand.weekday()] in weekdays):
                return cand.astimezone(UTC)
        raise ValueError("no matching day")

    def _trig_time(self, t: dict[str, Any], cb: Callable, variables: dict[str, Any]) -> Callable[[], None]:
        cancels: list[Callable[[], None]] = []
        days = [str(d).lower()[:3] for d in _as_list(t.get("weekday"))] or None

        def fire_at(at: datetime, label: str) -> None:
            cb({"now": at.astimezone(self.tz), "description": f"time {label}", "entity_id": None})

        for spec in _as_list(t["at"]):
            offset = timedelta(0)
            if isinstance(spec, dict):
                offset = parse_duration(spec.get("offset"))
                spec = spec["entity_id"]
            if isinstance(spec, str) and "." in spec and ":" not in spec:
                cancels.append(self._time_entity_trigger(spec, offset, days, cb))
                continue
            at_t = parse_time(spec)
            cancels.append(self._schedule_series(
                self.clock.now(), lambda after, at_t=at_t: self.next_local_time(after, at_t, days),
                lambda when, s=spec: fire_at(when, str(s))))

        return lambda: [c() for c in cancels]

    def _time_entity_trigger(self, entity_id: str, offset: timedelta, days: list[str] | None,
                             cb: Callable) -> Callable[[], None]:
        """``at: input_datetime.x`` or a timestamp sensor: re-armed whenever that entity changes."""
        holder: dict[str, Any] = {"cancel": None}

        def compute(after: datetime) -> datetime | None:
            st = self.hub.states.get(entity_id)
            if st is None or st.state in (UNKNOWN, UNAVAILABLE, ""):
                return None
            if st.domain == "input_datetime":
                has_date = st.attributes.get("has_date", " " in st.state or "-" in st.state)
                has_time = st.attributes.get("has_time", ":" in st.state)
                if has_date:
                    d = date.fromisoformat(st.state.split(" ")[0])
                    tm = parse_time(st.state.split(" ")[1]) if has_time and " " in st.state else time(0, 0)
                    when = datetime.combine(d, tm, tzinfo=self.tz).astimezone(UTC) + offset
                    return when if when > after else None
                tm = parse_time(st.state)
                return self.next_local_time(after - offset, tm, days) + offset
            try:
                when = datetime.fromisoformat(st.state)
            except ValueError:
                return None
            if when.tzinfo is None:
                when = when.replace(tzinfo=self.tz)
            when = when.astimezone(UTC) + offset
            return when if when > after else None

        def arm(after: datetime) -> None:
            if holder["cancel"]:
                holder["cancel"]()
                holder["cancel"] = None
            when = compute(after)
            if when is None:
                return

            def run(at: datetime) -> None:
                cb({"now": at.astimezone(self.tz), "entity_id": entity_id, "description": f"time {entity_id}"})
                arm(at)

            holder["cancel"] = self.scheduler.at(when, run)

        arm(self.clock.now())
        unsub = self._sub_state(lambda *_a: arm(self.clock.now()), [entity_id])

        def detach() -> None:
            unsub()
            if holder["cancel"]:
                holder["cancel"]()

        return detach

    def _trig_time_pattern(self, t: dict[str, Any], cb: Callable, variables: dict[str, Any]) -> Callable[[], None]:
        hours, minutes, seconds = t.get("hours"), t.get("minutes"), t.get("seconds")
        if hours is None and minutes is None and seconds is None:
            raise Unsupported("time_pattern needs hours, minutes or seconds")
        if hours is not None and minutes is None:
            minutes = 0
        if (hours is not None or minutes is not None) and seconds is None:
            seconds = 0
        hs = _pattern_values(hours, 24)
        ms = _pattern_values(minutes, 60)
        ss = _pattern_values(seconds, 60)

        def nxt(after: datetime) -> datetime | None:
            local = after.astimezone(self.tz)
            for day in range(0, 3):
                d = local.date() + timedelta(days=day)
                for h in hs:
                    if day == 0 and h < local.hour:
                        continue
                    for m in ms:
                        if day == 0 and h == local.hour and m < local.minute:
                            continue
                        for s in ss:
                            cand = datetime(d.year, d.month, d.day, h, m, s, tzinfo=self.tz)
                            if cand > local:
                                return cand.astimezone(UTC)
            return None

        return self._schedule_series(self.clock.now(), nxt, lambda at: cb(
            {"now": at.astimezone(self.tz), "description": "time pattern"}))

    def next_sun_event(self, after: datetime, event: str, offset: timedelta) -> datetime | None:
        local_day = after.astimezone(self.tz).date()
        best = None
        for delta in range(-1, 4):
            ev = self._sun_times(local_day + timedelta(days=delta))[event]
            if ev is None:
                continue
            when = ev + offset
            if when > after and (best is None or when < best):
                best = when
        return best

    def _trig_sun(self, t: dict[str, Any], cb: Callable, variables: dict[str, Any]) -> Callable[[], None]:
        event = str(t["event"])
        offset = parse_duration(t.get("offset"))
        self._sun_times(self.clock.now().date())  # raises Unsupported without a location
        return self._schedule_series(self.clock.now(), lambda after: self.next_sun_event(after, event, offset),
                                     lambda at: cb({"event": event, "offset": offset,
                                                    "description": f"sun {event}"}))

    # -- services --------------------------------------------------------------------------
    def _register_services(self) -> None:
        if self._services_registered:
            return
        self._services_registered = True
        reg = self.hub.register_service
        reg("automation", "trigger", self._svc_automation_trigger,
            {"skip_condition": {}, "variables": {}})
        reg("automation", "turn_on", self._svc_automation_on)
        reg("automation", "turn_off", self._svc_automation_off, {"stop_actions": {}})
        reg("automation", "toggle", self._svc_automation_toggle)
        reg("automation", "reload", self._svc_reload)
        reg("script", "turn_on", self._svc_script_on, {"variables": {}})
        reg("script", "turn_off", self._svc_script_off)
        reg("script", "toggle", self._svc_script_toggle)
        reg("script", "reload", self._svc_reload)
        reg("scene", "turn_on", self._svc_scene_on, {"transition": {}})
        reg("scene", "apply", self._svc_scene_apply, {"entities": {}, "transition": {}})
        reg("scene", "reload", self._svc_reload)

    def _register_script_services(self) -> None:
        # HA names a script's service after its key, whatever its entity was renamed to.
        for sid, item in self.scripts.items():
            obj = sid

            async def handler(hub: Any, call: ServiceCall, sid: str = sid) -> Any:
                return await self._svc_script_direct(sid, call)

            self.hub.register_service("script", obj, handler,
                                      {k: (v if isinstance(v, dict) else {}) for k, v in item.fields.items()})

    def _items_for(self, call: ServiceCall, pool: dict[str, Item]) -> list[Item]:
        by_entity = {i.entity_id: i for i in pool.values()}
        return [by_entity[e] for e in call.entity_ids if e in by_entity]

    async def _svc_automation_trigger(self, hub: Any, call: ServiceCall) -> None:
        skip = call.data.get("skip_condition", True)
        for item in self._items_for(call, self.automations):
            if item.runnable:
                await self.trigger(item.id, call.data.get("variables") or {}, skip_condition=bool(skip))

    async def _svc_automation_on(self, hub: Any, call: ServiceCall) -> None:
        for item in self._items_for(call, self.automations):
            await self.set_enabled(item, True)

    async def _svc_automation_off(self, hub: Any, call: ServiceCall) -> None:
        stop = bool(call.data.get("stop_actions", True))
        for item in self._items_for(call, self.automations):
            await self.set_enabled(item, False, stop_actions=stop)

    async def _svc_automation_toggle(self, hub: Any, call: ServiceCall) -> None:
        for item in self._items_for(call, self.automations):
            await self.set_enabled(item, not item.enabled)

    async def _svc_reload(self, hub: Any, call: ServiceCall) -> None:
        await self.reload()

    async def _svc_script_direct(self, sid: str, call: ServiceCall) -> Any:
        item = self.scripts.get(sid)
        if item is None:
            raise ServiceNotFound(f"script.{sid} not found")
        if not item.runnable:
            raise HubError(f"script.{sid} is disabled: {item.unsupported_reason}")
        data = {k: v for k, v in call.data.items() if k not in ("entity_id", "target", "area_id", "device_id")}
        task = self._start_run(item, self._script_variables(item, data))
        if task is None:
            return None
        response = await task
        return response if call.return_response else None

    async def _svc_script_on(self, hub: Any, call: ServiceCall) -> None:
        variables = call.data.get("variables") or {}
        for item in self._items_for(call, self.scripts):
            if item.runnable:
                self._start_run(item, self._script_variables(item, dict(variables)))

    async def _svc_script_off(self, hub: Any, call: ServiceCall) -> None:
        for item in self._items_for(call, self.scripts):
            await self._cancel_runs(item)

    async def _svc_script_toggle(self, hub: Any, call: ServiceCall) -> None:
        for item in self._items_for(call, self.scripts):
            if item.runs:
                await self._cancel_runs(item)
            elif item.runnable:
                self._start_run(item, self._script_variables(item, {}))

    async def _svc_scene_on(self, hub: Any, call: ServiceCall) -> None:
        by_entity = {s.entity_id: s for s in self.scenes.values()}
        for eid in call.entity_ids:
            scene = by_entity.get(eid)
            if scene is None:
                continue
            await self.apply_scene(scene.entities, call.data.get("transition"))
            scene.last_activated = self.clock.now()
            self._publish_scene(scene)

    async def _svc_scene_apply(self, hub: Any, call: ServiceCall) -> None:
        await self.apply_scene(call.data.get("entities") or {}, call.data.get("transition"))

    async def apply_scene(self, entities: dict[str, Any], transition: Any = None) -> None:
        """Reproduce each entity's state with the domain's own services (HA's reproduce_state)."""
        jobs = []
        for eid, spec in entities.items():
            for domain, service, data in _reproduce(eid, spec, transition):
                jobs.append(self.hub.call_service(domain, service, data))
        results = await asyncio.gather(*jobs, return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException):
                log.warning("scene: %s", r)

    # -- states ----------------------------------------------------------------------------
    def _friendly(self, entity_id: str, fallback: str) -> str:
        ent = getattr(self.hub, "entities", {}).get(entity_id)
        return (ent.name if ent is not None and ent.name else None) or fallback

    def _publish(self, item: Item) -> None:
        attrs: dict[str, Any] = {
            "last_triggered": item.last_triggered.isoformat() if item.last_triggered else None,
            "mode": item.mode, "current": len(item.runs),
            "friendly_name": self._friendly(item.entity_id, item.alias),
        }
        if item.mode in ("queued", "parallel"):
            attrs["max"] = item.max
        if item.icon:
            attrs["icon"] = item.icon
        if item.kind == "automation":
            attrs["id"] = item.id
            if item.unsupported_reason:
                attrs["unsupported_reason"] = item.unsupported_reason
            state = "on" if item.enabled and item.runnable else "off"
        else:
            if item.unsupported_reason:
                attrs["unsupported_reason"] = item.unsupported_reason
            state = "on" if item.runs else "off"
        self.hub.publish(item.entity_id, state, attrs)

    def _publish_scene(self, scene: Scene) -> None:
        attrs: dict[str, Any] = {"entity_id": list(scene.entities), "id": scene.id,
                                 "friendly_name": self._friendly(scene.entity_id, scene.name)}
        if scene.icon:
            attrs["icon"] = scene.icon
        self.hub.publish(scene.entity_id, scene.last_activated.isoformat() if scene.last_activated else UNKNOWN,
                         attrs)

    # -- the scheduler (nielsfaber) -------------------------------------------------------------
    def _install_schedule_driver(self) -> None:
        """Route ``switch.turn_on/off`` on schedule entities here, through the scheduler config entry."""
        entries = [e for e in getattr(self.hub, "config_entries", {}).values() if e.domain == "scheduler"]
        for entry in entries:
            self.hub.drivers[entry.entry_id] = _ScheduleDriver(self.hub, entry, self)

    def _workdays(self) -> tuple[list[str], str | None]:
        st = self.hub.states.get("binary_sensor.workday_sensor")
        days = WEEKDAYS[:5]
        if st is not None and isinstance(st.attributes.get("workdays"), list):
            days = [str(d)[:3] for d in st.attributes["workdays"]]
        return days, (st.state if st is not None and st.state in ("on", "off") else None)

    def _day_ok(self, s: Schedule, local: datetime) -> bool:
        d = local.date()
        if s.start_date and d < s.start_date:
            return False
        if s.end_date and d > s.end_date:
            return False
        day = WEEKDAYS[local.weekday()]
        workdays, today_state = self._workdays()
        if today_state is not None and d == self.clock.now().astimezone(self.tz).date():
            if "workday" in s.weekdays:
                return today_state == "on"
            if "weekend" in s.weekdays:
                return today_state == "off"
        if "daily" in s.weekdays or not s.weekdays:
            return True
        if "workday" in s.weekdays and day in workdays:
            return True
        if "weekend" in s.weekdays and day not in workdays:
            return True
        return day in s.weekdays

    def _slot_instant(self, value: Any, day: date) -> datetime | None:
        m = _SUN_SLOT.match(str(value))
        if m:
            ev = self._sun_times(day)[m.group(1)]
            if ev is None:
                return None
            off = parse_duration(m.group(3)) if m.group(3) else timedelta(0)
            return ev - off if m.group(2) == "-" else ev + off
        return datetime.combine(day, parse_time(value).replace(tzinfo=None), tzinfo=self.tz).astimezone(UTC)

    def schedule_next(self, s: Schedule, after: datetime) -> tuple[datetime, int] | None:
        local = after.astimezone(self.tz)
        horizon = 400
        if s.start_date and s.start_date > local.date():
            horizon += (s.start_date - local.date()).days
        for delta in range(0, horizon):
            d = local.date() + timedelta(days=delta)
            if s.end_date and d > s.end_date:
                return None
            day_dt = datetime.combine(d, time(12), tzinfo=self.tz)
            if not self._day_ok(s, day_dt):
                continue
            best = None
            for idx, slot in enumerate(s.timeslots):
                when = self._slot_instant(slot.get("start"), d)
                if when is not None and when > after and (best is None or when < best[0]):
                    best = (when, idx)
            if best:
                return best
        return None

    def current_slot(self, s: Schedule, now: datetime) -> tuple[int, datetime] | None:
        """The timeslot ``now`` falls in (start on a valid day, stop not reached), with its start."""
        local = now.astimezone(self.tz)
        for delta in (0, -1):
            d = local.date() + timedelta(days=delta)
            if not self._day_ok(s, datetime.combine(d, time(12), tzinfo=self.tz)):
                continue
            for idx, slot in enumerate(s.timeslots):
                start = self._slot_instant(slot.get("start"), d)
                if start is None or start > now:
                    continue
                stop_v = slot.get("stop")
                if stop_v is None:
                    continue
                stop = self._slot_instant(stop_v, d)
                if stop is None:
                    continue
                if stop <= start:
                    stop += timedelta(days=1)  # "21:00 - 00:00" ends at the next midnight
                if start <= now < stop:
                    return idx, start
        return None

    def _arm_schedule(self, s: Schedule) -> None:
        if s.cancel:
            s.cancel()
            s.cancel = None
        s.next_trigger, s.next_slot = None, None
        if not s.enabled or s.unsupported_reason or not self._running:
            self._publish_schedule(s)
            return
        try:
            nxt = self.schedule_next(s, self.clock.now())
        except Unsupported as exc:
            s.unsupported_reason = str(exc)
            nxt = None
        if nxt is not None:
            s.next_trigger, s.next_slot = nxt

            def run(at: datetime, idx: int = nxt[1]) -> None:
                s.cancel = None
                self._spawn_task(self._run_slot(s, idx, at))
                self._arm_schedule_after(s, at)

            s.cancel = self.scheduler.at(nxt[0], run)
        self._publish_schedule(s)

    def _arm_schedule_after(self, s: Schedule, at: datetime) -> None:
        nxt = self.schedule_next(s, at)
        s.next_trigger, s.next_slot = nxt if nxt else (None, None)
        if nxt is not None:
            def run(when: datetime, idx: int = nxt[1]) -> None:
                s.cancel = None
                self._spawn_task(self._run_slot(s, idx, when))
                self._arm_schedule_after(s, when)

            s.cancel = self.scheduler.at(nxt[0], run)
        self._publish_schedule(s)

    def _spawn_task(self, coro: Awaitable[Any]) -> None:
        task = _spawn(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _slot_conditions_ok(self, slot: dict[str, Any]) -> bool:
        conds = slot.get("conditions") or []
        if not conds:
            return True
        results = []
        for c in conds:
            st = self.hub.states.get(str(c.get("entity_id")))
            value = st.state if st is not None else None
            want = c.get("value")
            match = c.get("match_type") or "is"
            ok = False
            if value is not None:
                if match == "is":
                    ok = str(value) == str(want)
                elif match == "not":
                    ok = str(value) != str(want)
                elif match in ("below", "above") and templating.is_number(value) and templating.is_number(want):
                    ok = float(value) < float(want) if match == "below" else float(value) > float(want)
            results.append(ok)
        return any(results) if slot.get("condition_type") == "or" else all(results)

    async def _run_slot(self, s: Schedule, idx: int, at: datetime) -> None:
        slot = s.timeslots[idx]
        if not self._slot_conditions_ok(slot):
            if slot.get("track_conditions"):
                self._track_slot_conditions(s, idx, at)
            return
        await self._run_slot_actions(s, idx, at)

    async def _run_slot_actions(self, s: Schedule, idx: int, at: datetime) -> None:
        slot = s.timeslots[idx]
        s.last_run = at
        if self.store is not None:
            try:
                self.store.set_kv(f"schedule:{s.id}", {"enabled": s.enabled, "last_run": at.isoformat()})
            except Exception:
                log.exception("store write failed for %s", s.entity_id)
        errors = []
        for act in slot.get("actions") or []:
            svc = str(act.get("service") or act.get("action"))
            domain, service = svc.split(".", 1)
            data = dict(act.get("service_data") or act.get("data") or {})
            target = act.get("target") or {}
            eid = act.get("entity_id") or target.get("entity_id")
            if eid:
                data["entity_id"] = eid
            try:
                await self.hub.call_service(domain, service, data)
            except Exception as exc:
                errors.append(f"{svc} {eid}: {exc}")
        s.last_error = "; ".join(errors) or None
        if errors:
            log.warning("%s: %s", s.entity_id, s.last_error)
        self._publish_schedule(s)

    def _track_slot_conditions(self, s: Schedule, idx: int, start: datetime) -> None:
        """``track_conditions``: run the slot as soon as its conditions hold, until the slot ends."""
        slot = s.timeslots[idx]
        local_day = start.astimezone(self.tz).date()
        stop = self._slot_instant(slot.get("stop"), local_day) if slot.get("stop") else None
        if stop is not None and stop <= start:
            stop += timedelta(days=1)
        ids = [str(c.get("entity_id")) for c in slot.get("conditions") or []]
        holder: dict[str, Any] = {}

        def finish() -> None:
            holder["unsub"]()
            if holder.get("cancel"):
                holder["cancel"]()

        def on_change(*_a: Any) -> None:
            if self._slot_conditions_ok(slot):
                finish()
                self._spawn_task(self._run_slot_actions(s, idx, self.clock.now()))

        holder["unsub"] = self._sub_state(on_change, ids)
        if stop is not None:
            holder["cancel"] = self.scheduler.at(stop, lambda _w: holder["unsub"]())

    async def _startup_schedules(self) -> None:
        """The scheduler runs the slot in progress at startup, unless it already ran before shutdown."""
        now = self.clock.now()
        for s in self.schedules.values():
            if not s.enabled or s.unsupported_reason:
                continue
            try:
                cur = self.current_slot(s, now)
            except Unsupported:
                continue
            if cur is None:
                continue
            idx, start = cur
            ran_by = max([d for d in (s.last_run, self._time_shutdown) if d is not None], default=None)
            if ran_by is not None and start <= ran_by:
                continue
            self._spawn_task(self._run_slot(s, idx, start))

    async def set_schedule_enabled(self, s: Schedule, enabled: bool) -> None:
        s.enabled = enabled
        if self.store is not None:
            try:
                self.store.set_kv(f"schedule:{s.id}", {"enabled": enabled,
                                                      "last_run": s.last_run.isoformat() if s.last_run else None})
            except Exception:
                log.exception("store write failed for %s", s.entity_id)
        self._arm_schedule(s)

    def _publish_schedule(self, s: Schedule) -> None:
        slots = []
        for slot in s.timeslots:
            slots.append(f"{slot.get('start')} - {slot.get('stop')}" if slot.get("stop") else str(slot.get("start")))
        ents = sorted({str(a.get("entity_id") or (a.get("target") or {}).get("entity_id"))
                       for slot in s.timeslots for a in slot.get("actions") or []})
        next_actions = []
        if s.next_slot is not None:
            next_actions = [str(a.get("service") or a.get("action")) for a in s.timeslots[s.next_slot].get("actions")
                            or []]
        attrs = {
            "friendly_name": self._friendly(s.entity_id, s.name), "icon": "mdi:calendar-clock",
            "weekdays": list(s.weekdays), "timeslots": slots, "entities": ents, "actions": next_actions,
            "next_slot": s.next_slot, "next_trigger": s.next_trigger.astimezone(self.tz).isoformat()
            if s.next_trigger else None, "start_date": s.start_date.isoformat() if s.start_date else None,
            "end_date": s.end_date.isoformat() if s.end_date else None, "repeat_type": s.repeat_type,
            "tags": [],
        }
        if s.unsupported_reason:
            attrs["unsupported_reason"] = s.unsupported_reason
        self.hub.publish(s.entity_id, "on" if s.enabled and not s.unsupported_reason else "off", attrs)


_TIMEOUT = object()
_SUN_SLOT = re.compile(r"^(sunrise|sunset)(?:([+-])(\d{1,2}:\d{2}(?::\d{2})?))?$")


def _pattern_values(spec: Any, size: int) -> list[int]:
    if spec is None or str(spec) == "*":
        return list(range(size))
    s = str(spec).strip()
    if s.startswith("/"):
        step = int(s[1:])
        if step <= 0:
            raise Unsupported(f"invalid time_pattern {spec!r}")
        return list(range(0, size, step))
    v = int(s)
    if not 0 <= v < size:
        raise Unsupported(f"invalid time_pattern {spec!r}")
    return [v]


class _ScheduleDriver(Driver):
    """The scheduler config entry's 'driver': schedules are switches the engine owns."""

    platform = "scheduler"
    poll_interval = None

    def __init__(self, hub: Any, entry: Any, engine: AutomationEngine) -> None:
        super().__init__(hub, entry)
        self.engine = engine

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        sched = next((s for s in self.engine.schedules.values() if s.entity_id == entity.entity_id), None)
        if sched is None or domain != "switch":
            raise ServiceNotFound(f"scheduler cannot run {domain}.{service} on {entity.entity_id}")
        if service == "turn_on":
            await self.engine.set_schedule_enabled(sched, True)
        elif service == "turn_off":
            await self.engine.set_schedule_enabled(sched, False)
        elif service == "toggle":
            await self.engine.set_schedule_enabled(sched, not sched.enabled)
        else:
            raise ServiceNotFound(f"scheduler cannot run switch.{service}")


# -- scenes ----------------------------------------------------------------------------------

_LIGHT_ATTRS = ("brightness", "color_temp_kelvin", "rgb_color", "hs_color", "xy_color", "rgbw_color", "effect")


def _reproduce(entity_id: str, spec: Any, transition: Any) -> list[tuple[str, str, dict[str, Any]]]:
    domain = entity_id.split(".", 1)[0]
    if isinstance(spec, dict):
        state = _state_str(spec.get("state"))
        attrs = {k: v for k, v in spec.items() if k != "state"}
    else:
        state, attrs = _state_str(spec), {}
    base = {"entity_id": entity_id}
    if domain in ("light", "switch", "fan", "input_boolean", "siren", "humidifier", "remote", "automation"):
        if state == "off":
            return [(domain, "turn_off", base)]
        data = dict(base)
        if domain == "light":
            data.update({k: attrs[k] for k in _LIGHT_ATTRS if attrs.get(k) is not None})
            if transition is not None:
                data["transition"] = transition
        if domain == "fan" and attrs.get("percentage") is not None:
            data["percentage"] = attrs["percentage"]
        return [(domain, "turn_on", data)]
    if domain == "media_player":
        if state in ("off", "standby"):
            return [(domain, "turn_off", base)]
        calls = [(domain, "turn_on", base)]
        if attrs.get("volume_level") is not None:
            calls.append((domain, "volume_set", {**base, "volume_level": attrs["volume_level"]}))
        if attrs.get("source"):
            calls.append((domain, "select_source", {**base, "source": attrs["source"]}))
        return calls
    if domain == "cover":
        if attrs.get("current_position") is not None:
            return [(domain, "set_cover_position", {**base, "position": attrs["current_position"]})]
        return [(domain, "open_cover" if state in ("open", "opening") else "close_cover", base)]
    if domain == "lock":
        return [(domain, "lock" if state == "locked" else "unlock", base)]
    if domain == "climate":
        calls = [(domain, "set_hvac_mode", {**base, "hvac_mode": state})] if state else []
        temp = {k: attrs[k] for k in ("temperature", "target_temp_high", "target_temp_low") if attrs.get(k) is not None}
        if temp:
            calls.append((domain, "set_temperature", {**base, **temp}))
        return calls
    if domain in ("number", "input_number"):
        return [(domain, "set_value", {**base, "value": state})]
    if domain in ("select", "input_select"):
        return [(domain, "select_option", {**base, "option": state})]
    if domain == "script":
        return [(domain, "turn_on" if state == "on" else "turn_off", base)]
    log.warning("scene: cannot reproduce %s", entity_id)
    return []


# -- parsing ---------------------------------------------------------------------------------

_ACTION_KEYS = ("delay", "wait_template", "wait_for_trigger", "choose", "if", "repeat", "variables", "stop",
                "condition", "event", "parallel", "sequence", "scene")


class _ParseContext:
    """Normalises one item's triggers, conditions and actions, checking what they reference."""

    def __init__(self, engine: AutomationEngine, item: Item) -> None:
        self.engine = engine
        self.item = item

    # references
    def need_entities(self, value: Any, where: str) -> None:
        for eid in _entity_list(value):
            if _is_template(eid):
                continue
            if not self.engine.known_entity(eid):
                raise Unsupported(f"{where} names {eid}, which no longer exists")

    def need_helper(self, domain: str, what: str) -> None:
        if self.engine.helper_missing(domain):
            raise Unsupported(f"{what} needs the {domain} integration, which has no entities any more")

    # triggers
    def trigger(self, raw: Any, idx: int) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            raise Unsupported(f"invalid trigger {raw!r}")
        t = dict(raw)
        if t.get("enabled") is False:
            return None
        kind = t.pop("trigger", None) or t.pop("platform", None)
        t.pop("platform", None)
        if kind == "device":
            t = self.device_trigger(t)
            kind = t.pop("trigger")
        t["trigger"] = kind
        t.setdefault("id", str(idx))
        t["id"] = str(t["id"])
        t["idx"] = str(idx)
        if kind in UNSUPPORTED_TRIGGERS:
            raise Unsupported(f"{kind} triggers are not supported")
        if kind in ("state", "numeric_state"):
            if not t.get("entity_id"):
                raise Unsupported(f"{kind} trigger without entity_id")
            self.need_entities(t["entity_id"], f"{kind} trigger")
            if kind == "numeric_state":
                for k in ("above", "below"):
                    if isinstance(t.get(k), str) and "." in t[k] and not templating.is_number(t[k]):
                        self.need_entities(t[k], f"numeric_state {k}")
        elif kind == "zone":
            self.need_entities(t.get("entity_id"), "zone trigger")
            self.need_entities(t.get("zone"), "zone trigger")
            if t.get("event", "enter") not in ("enter", "leave"):
                raise Unsupported(f"zone trigger event {t.get('event')!r}")
        elif kind == "time":
            for spec in _as_list(t.get("at")):
                ent = spec.get("entity_id") if isinstance(spec, dict) else spec
                if isinstance(ent, str) and "." in ent and ":" not in ent:
                    self.need_entities(ent, "time trigger")
                else:
                    parse_time(ent)
            if not _as_list(t.get("at")):
                raise Unsupported("time trigger without 'at'")
        elif kind == "time_pattern":
            for k in ("hours", "minutes", "seconds"):
                if t.get(k) is not None:
                    _pattern_values(t[k], 24 if k == "hours" else 60)
        elif kind == "sun":
            if t.get("event") not in ("sunrise", "sunset"):
                raise Unsupported(f"sun trigger event {t.get('event')!r}")
            loc = getattr(self.engine.hub, "location", None) or {}
            if "latitude" not in loc or "longitude" not in loc:
                raise Unsupported("sun trigger, but the hub has no location")
            parse_duration(t.get("offset"))
        elif kind == "template":
            if not t.get("value_template"):
                raise Unsupported("template trigger without value_template")
        elif kind == "event":
            types = _as_list(t.get("event_type"))
            if not types:
                raise Unsupported("event trigger without event_type")
            for et in types:
                if isinstance(et, str) and "." in et:
                    self.need_helper(et.split(".", 1)[0], f"event trigger {et}")
        elif kind == "homeassistant":
            if t.get("event") not in ("start", "shutdown"):
                raise Unsupported(f"homeassistant trigger event {t.get('event')!r}")
        else:
            raise Unsupported(f"{kind!r} triggers are not supported")
        return t

    def device_trigger(self, t: dict[str, Any]) -> dict[str, Any]:
        domain = t.get("domain")
        typ = str(t.get("type") or "")
        eid = self.engine.resolve_registry_id(t.get("entity_id"))
        out: dict[str, Any] = {"entity_id": eid}
        for k in ("id", "alias", "for", "enabled"):
            if k in t:
                out[k] = t[k]
        if domain == "binary_sensor":
            on = not (typ.startswith(("not_", "no_")) or typ == "turned_off")
            out.update({"trigger": "state", "from": "off" if on else "on", "to": "on" if on else "off"})
        elif domain == "sensor":
            out.update({"trigger": "numeric_state", **{k: t[k] for k in ("above", "below") if k in t}})
            if "above" not in out and "below" not in out:
                raise Unsupported(f"sensor device trigger {typ!r} without above/below")
        elif domain in TOGGLE_DOMAINS and typ in ("turned_on", "turned_off", "changed_states"):
            out["trigger"] = "state"
            if typ != "changed_states":
                out["to"] = "on" if typ == "turned_on" else "off"
            else:
                out["to"] = None
        elif domain == "media_player" and typ in ("idle", "paused", "playing", "buffering"):
            out.update({"trigger": "state", "to": typ})
        elif domain == "cover" and typ in ("opened", "closed", "opening", "closing"):
            out.update({"trigger": "state", "to": "open" if typ == "opened" else typ})
        elif domain == "cover" and typ in ("position", "tilt_position"):
            out.update({"trigger": "numeric_state", "attribute": "current_" + typ,
                        **{k: t[k] for k in ("above", "below") if k in t}})
        elif domain == "lock" and typ in ("locked", "unlocked", "jammed", "locking", "unlocking", "opening", "open"):
            out.update({"trigger": "state", "to": typ})
        elif domain == "climate" and typ == "hvac_mode_changed":
            out.update({"trigger": "state", "to": t.get("to")})
        elif domain == "climate" and typ in ("current_temperature_changed", "current_humidity_changed"):
            out.update({"trigger": "numeric_state", "attribute": typ.replace("_changed", ""),
                        **{k: t[k] for k in ("above", "below") if k in t}})
        elif domain == "device_tracker" and typ in ("enters", "leaves"):
            out.update({"trigger": "zone", "zone": t.get("zone"), "event": "enter" if typ == "enters" else "leave"})
        elif domain == "vacuum" and typ in ("cleaning", "docked"):
            out.update({"trigger": "state", "to": typ})
        else:
            raise Unsupported(f"device trigger {domain}/{typ} cannot be translated")
        return out

    # conditions
    def conditions(self, raw: Any) -> list[dict[str, Any]]:
        return [c for c in (self.condition(x) for x in _as_list(raw)) if c is not None]

    def condition(self, raw: Any) -> dict[str, Any] | None:
        if isinstance(raw, str):
            return {"condition": "template", "value_template": raw}
        if not isinstance(raw, dict):
            raise Unsupported(f"invalid condition {raw!r}")
        c = dict(raw)
        if c.get("enabled") is False:
            return None
        if "condition" not in c:
            for short in ("and", "or", "not"):
                if short in c:
                    return {"condition": short, "conditions": self.conditions(c[short])}
            raise Unsupported(f"condition without a type: {c}")
        kind = c["condition"]
        if kind in ("and", "or", "not"):
            c["conditions"] = self.conditions(c.get("conditions"))
        elif kind == "device":
            c = self.device_condition(c)
        elif kind == "state":
            self.need_entities(c.get("entity_id"), "state condition")
            if "state" not in c:
                raise Unsupported("state condition without state")
        elif kind == "numeric_state":
            self.need_entities(c.get("entity_id"), "numeric_state condition")
            for k in ("above", "below"):
                if isinstance(c.get(k), str) and "." in c[k] and not templating.is_number(c[k]):
                    self.need_entities(c[k], f"numeric_state {k}")
        elif kind == "time":
            for k in ("after", "before"):
                v = c.get(k)
                if isinstance(v, str) and "." in v and ":" not in v:
                    self.need_entities(v, "time condition")
                elif v is not None:
                    parse_time(v)
        elif kind == "sun":
            loc = getattr(self.engine.hub, "location", None) or {}
            if "latitude" not in loc:
                raise Unsupported("sun condition, but the hub has no location")
        elif kind == "zone":
            self.need_entities(c.get("entity_id"), "zone condition")
            self.need_entities(c.get("zone"), "zone condition")
        elif kind == "template":
            if not c.get("value_template"):
                raise Unsupported("template condition without value_template")
        elif kind == "trigger":
            if c.get("id") is None:
                raise Unsupported("trigger condition without id")
        else:
            raise Unsupported(f"{kind!r} conditions are not supported")
        return c

    def device_condition(self, c: dict[str, Any]) -> dict[str, Any]:
        domain = c.get("domain")
        typ = str(c.get("type") or "")
        eid = self.engine.resolve_registry_id(c.get("entity_id"))
        base: dict[str, Any] = {"entity_id": eid}
        if c.get("for") is not None:
            base["for"] = c["for"]
        if domain == "binary_sensor" and typ.startswith("is_"):
            on = not (typ.startswith(("is_not_", "is_no_")) or typ == "is_off")
            return {"condition": "state", "state": "on" if on else "off", **base}
        if domain == "sensor" and typ.startswith("is_"):
            if "above" not in c and "below" not in c:
                raise Unsupported(f"sensor device condition {typ!r} without above/below")
            return {"condition": "numeric_state", **{k: c[k] for k in ("above", "below") if k in c},
                    "entity_id": eid}
        if domain in TOGGLE_DOMAINS and typ in ("is_on", "is_off"):
            return {"condition": "state", "state": typ[3:], **base}
        if domain == "media_player" and typ in ("is_idle", "is_paused", "is_playing", "is_buffering"):
            return {"condition": "state", "state": typ[3:], **base}
        if domain == "climate" and typ == "is_hvac_mode":
            return {"condition": "state", "state": c.get("hvac_mode"), **base}
        if domain == "climate" and typ == "is_preset_mode":
            return {"condition": "state", "attribute": "preset_mode", "state": c.get("preset_mode"), **base}
        if domain == "cover" and typ in ("is_open", "is_closed", "is_opening", "is_closing"):
            return {"condition": "state", "state": typ[3:], **base}
        if domain == "cover" and typ in ("is_position", "is_tilt_position"):
            return {"condition": "numeric_state", "attribute": "current_" + typ[3:],
                    **{k: c[k] for k in ("above", "below") if k in c}, "entity_id": eid}
        if domain == "lock" and typ in ("is_locked", "is_unlocked", "is_jammed", "is_locking", "is_unlocking",
                                        "is_open", "is_opening"):
            return {"condition": "state", "state": typ[3:], **base}
        if domain == "device_tracker" and typ in ("is_home", "is_not_home"):
            return {"condition": "state", "state": "home" if typ == "is_home" else "not_home", **base}
        raise Unsupported(f"device condition {domain}/{typ} cannot be translated")

    # actions
    def sequence(self, raw: Any) -> list[dict[str, Any]]:
        return [s for s in (self.action(a) for a in _as_list(raw)) if s is not None]

    def action(self, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            raise Unsupported(f"invalid action {raw!r}")
        a = dict(raw)
        if a.get("enabled") is False:
            return None
        if "action" in a or "service" in a or "service_template" in a:
            return self.service_action(a)
        if "device_id" in a and "domain" in a and "type" in a:
            return self.service_action(self.device_action(a))
        for key in _ACTION_KEYS:
            if key in a:
                return getattr(self, f"act_{key}")(a)
        raise Unsupported(f"unsupported action: {sorted(a)}")

    def service_action(self, a: dict[str, Any]) -> dict[str, Any]:
        action = a.pop("action", None) or a.pop("service", None) or a.pop("service_template", None)
        a.pop("service", None)
        a.pop("metadata", None)
        a["action"] = action
        a["_kind"] = "service"
        if not _is_template(action):
            name = str(action)
            if "." not in name:
                raise Unsupported(f"invalid service {name!r}")
            domain, service = name.split(".", 1)
            self.need_helper(domain, f"action {name}")
            if domain == "script" and service not in ("turn_on", "turn_off", "toggle", "reload"):
                self.item.calls_scripts.add(service)
            elif domain == "script":
                for eid in self._literal_targets(a):
                    self.item.calls_scripts.add(eid.split(".", 1)[1])
            self.check_targets(a, name)
        return a

    def _literal_targets(self, a: dict[str, Any]) -> list[str]:
        out = []
        for src in (a, a.get("target") or {}, a.get("data") or {}):
            if isinstance(src, dict):
                out += [e for e in _entity_list(src.get("entity_id")) if not _is_template(e)]
        return out

    def check_targets(self, a: dict[str, Any], name: str) -> None:
        """A call none of whose targets exist can only fail; one with some missing just warns."""
        if not self.engine._check_refs:
            return
        hub = self.engine.hub
        sources = [a, a.get("target") or {}, a.get("data") or {}]
        if any(isinstance(s, dict) and _is_template(str(s.get("entity_id") or "")) for s in sources):
            return
        named: list[str] = []
        found: list[str] = []
        for src in sources:
            if not isinstance(src, dict):
                continue
            for eid in _entity_list(src.get("entity_id")):
                if eid == "all" or eid.startswith(("script.", "automation.", "scene.")):
                    continue
                named.append(eid)
                if self.engine.known_entity(eid):
                    found.append(eid)
            for dev in _entity_list(src.get("device_id")):
                named.append(f"device {dev}")
                if dev in hub.devices:
                    found.append(dev)
            for area in _entity_list(src.get("area_id")):
                named.append(f"area {area}")
                if hub.area_id_by_name(area) is not None:
                    found.append(area)
            for k, v in src.items():
                if k != "entity_id" and k.endswith("entity_id") and isinstance(v, (str, list)):
                    for eid in _entity_list(v):
                        if not _is_template(eid) and not self.engine.known_entity(eid):
                            raise Unsupported(f"{name} sends {k} {eid}, which no longer exists")
        missing = [n for n in named if n not in found and n.split(" ", 1)[-1] not in found]
        if named and not found:
            raise Unsupported(f"{name} targets only {', '.join(named)}, none of which exist")
        if missing:
            self.item.warnings.append(f"{name}: {', '.join(missing)} no longer exist")

    def device_action(self, a: dict[str, Any]) -> dict[str, Any]:
        domain = str(a.get("domain"))
        typ = str(a.get("type") or "")
        eid = self.engine.resolve_registry_id(a.get("entity_id"))
        out: dict[str, Any] = {"entity_id": eid}
        for k in ("continue_on_error", "alias"):
            if k in a:
                out[k] = a[k]
        data: dict[str, Any] = {}
        if domain in TOGGLE_DOMAINS and typ in ("turn_on", "turn_off", "toggle"):
            svc = f"{domain}.{typ}"
        elif domain == "light" and typ in ("brightness_increase", "brightness_decrease"):
            svc, data = "light.turn_on", {"brightness_step_pct": 10 if typ.endswith("increase") else -10}
        elif domain == "light" and typ == "flash":
            svc, data = "light.turn_on", {"flash": a.get("flash", "short")}
        elif domain == "climate" and typ == "set_hvac_mode":
            svc, data = "climate.set_hvac_mode", {"hvac_mode": a.get("hvac_mode")}
        elif domain == "climate" and typ == "set_preset_mode":
            svc, data = "climate.set_preset_mode", {"preset_mode": a.get("preset_mode")}
        elif domain == "cover" and typ in ("open", "close", "stop"):
            svc = f"cover.{typ}_cover"
        elif domain == "cover" and typ == "set_position":
            svc, data = "cover.set_cover_position", {"position": a.get("position")}
        elif domain == "lock" and typ in ("lock", "unlock", "open"):
            svc = f"lock.{typ}"
        elif domain == "button" and typ == "press":
            svc = "button.press"
        elif domain == "number" and typ == "set_value":
            svc, data = "number.set_value", {"value": a.get("value")}
        elif domain == "select" and typ == "select_option":
            svc, data = "select.select_option", {"option": a.get("option")}
        elif domain == "media_player" and typ in ("play", "pause", "stop", "next_track", "previous_track"):
            svc = f"media_player.media_{typ}"
        elif domain == "vacuum" and typ in ("clean", "dock"):
            svc = "vacuum.start" if typ == "clean" else "vacuum.return_to_base"
        else:
            raise Unsupported(f"device action {domain}/{typ} cannot be translated")
        out["action"] = svc
        if data:
            out["data"] = data
        return out

    def act_delay(self, a: dict[str, Any]) -> dict[str, Any]:
        if not any(True for _ in templating.iter_templates(a["delay"])):
            parse_duration(a["delay"])  # a literal delay that cannot parse is a config error now, not at 3 am
        return {**a, "_kind": "delay"}

    def act_wait_template(self, a: dict[str, Any]) -> dict[str, Any]:
        return {**a, "_kind": "wait_template"}

    def act_wait_for_trigger(self, a: dict[str, Any]) -> dict[str, Any]:
        trigs = [self.trigger(t, i) for i, t in enumerate(_as_list(a["wait_for_trigger"]))]
        return {**a, "wait_for_trigger": [t for t in trigs if t is not None], "_kind": "wait_for_trigger"}

    def act_choose(self, a: dict[str, Any]) -> dict[str, Any]:
        options = []
        for opt in _as_list(a["choose"]):
            options.append({"conditions": self.conditions(opt.get("conditions")),
                            "sequence": self.sequence(opt.get("sequence"))})
        return {**a, "choose": options, "default": self.sequence(a.get("default")), "_kind": "choose"}

    def act_if(self, a: dict[str, Any]) -> dict[str, Any]:
        return {**a, "if": self.conditions(a["if"]), "then": self.sequence(a.get("then")),
                "else": self.sequence(a.get("else")), "_kind": "if"}

    def act_repeat(self, a: dict[str, Any]) -> dict[str, Any]:
        rep = dict(a["repeat"])
        rep["sequence"] = self.sequence(rep.get("sequence"))
        for k in ("while", "until"):
            if k in rep:
                rep[k] = self.conditions(rep[k])
        if not any(k in rep for k in ("count", "for_each", "while", "until")):
            raise Unsupported("repeat needs count, for_each, while or until")
        return {**a, "repeat": rep, "_kind": "repeat"}

    def act_variables(self, a: dict[str, Any]) -> dict[str, Any]:
        return {**a, "variables": dict(a["variables"] or {}), "_kind": "variables"}

    def act_stop(self, a: dict[str, Any]) -> dict[str, Any]:
        return {**a, "_kind": "stop"}

    def act_condition(self, a: dict[str, Any]) -> dict[str, Any]:
        cond = self.condition({k: v for k, v in a.items() if k not in ("alias", "continue_on_error")})
        return {"_kind": "condition", "condition": cond or {"condition": "and", "conditions": []},
                "alias": a.get("alias")}

    def act_event(self, a: dict[str, Any]) -> dict[str, Any]:
        return {**a, "_kind": "event"}

    def act_parallel(self, a: dict[str, Any]) -> dict[str, Any]:
        branches = []
        for branch in _as_list(a["parallel"]):
            if isinstance(branch, dict) and "sequence" in branch and len(
                    [k for k in branch if k not in ("alias", "enabled", "continue_on_error")]) == 1:
                branches.append(self.sequence(branch["sequence"]))
            else:
                branches.append(self.sequence([branch]))
        return {**a, "parallel": branches, "_kind": "parallel"}

    def act_sequence(self, a: dict[str, Any]) -> dict[str, Any]:
        return {**a, "sequence": self.sequence(a["sequence"]), "_kind": "sequence"}

    def act_scene(self, a: dict[str, Any]) -> dict[str, Any]:
        return {**a, "_kind": "scene"}
