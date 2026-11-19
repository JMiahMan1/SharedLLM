"""Home Assistant's helpers, kept by the hub: toggles, numbers, texts, lists, dates, buttons, counters, timers.

HA keeps these in its own storage (``.storage/input_boolean`` and friends), not
in a config entry, so they have no driver of their own. Here one manager owns
all eight domains:

* ``input_boolean``, ``input_number``, ``input_text``, ``input_select``,
  ``input_datetime``, ``input_button``, ``counter`` and ``timer``, with HA's
  configuration fields, services, state values and attributes, so automations,
  scripts, templates and every HA client keep working unchanged.
* Each helper's configuration is kept in the store (``helpers``: domain ->
  HA's storage items), imported from HA's ``.storage`` files by the importer and
  changed through ``/hub/helpers`` (``helpers_api.py``).
* Values come back after a restart from the last recorded state (the runtime
  records every state), as HA's restore-state does. ``initial`` wins over the
  restored value, as in HA; a counter only restores with ``restore`` on, and a
  timer only with ``restore`` on, finishing at once if it ran out while the hub
  was down.
* Timers fire HA's events: ``timer.started``, ``timer.restarted``,
  ``timer.paused``, ``timer.cancelled``, ``timer.finished`` and
  ``timer.changed``, each with the timer's ``entity_id``.

Helper entities keep HA's registry shape (platform = the domain, unique id =
the helper's id, no config entry), so a renamed helper keeps its entity id and
the name a person gave it. Service calls reach the manager through the hub's
platform dispatch (``Hub.platform_drivers``).

A configuration that cannot be read is never guessed at: it is left out and
reported (``problems``) with the reason.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from services.homehub.core import UNKNOWN, EntityEntry, Hub, HubError, ServiceCall, ServiceNotFound

log = logging.getLogger("homehub.helpers")

DOMAINS = ("input_boolean", "input_number", "input_text", "input_select", "input_datetime", "input_button",
           "counter", "timer")
STORE_KEY = "helpers"

IDLE, ACTIVE, PAUSED = "idle", "active", "paused"
#: The longest a timer task sleeps before looking at the clock again (a clock step is noticed promptly).
MAX_SLEEP_S = 30.0

#: The services each domain answers, with the fields they take (``/api/services``).
SERVICES: dict[str, dict[str, dict[str, Any]]] = {
    "input_boolean": {"turn_on": {}, "turn_off": {}, "toggle": {}},
    "input_number": {"set_value": {"value": {"required": True, "selector": {"number": {}}}},
                     "increment": {}, "decrement": {}},
    "input_text": {"set_value": {"value": {"required": True, "selector": {"text": {}}}}},
    "input_select": {"select_option": {"option": {"required": True, "selector": {"text": {}}}},
                     "select_next": {"cycle": {"selector": {"boolean": {}}}},
                     "select_previous": {"cycle": {"selector": {"boolean": {}}}},
                     "select_first": {}, "select_last": {},
                     "set_options": {"options": {"required": True, "selector": {"object": {}}}}},
    "input_datetime": {"set_datetime": {"date": {"selector": {"date": {}}}, "time": {"selector": {"time": {}}},
                                        "datetime": {"selector": {"datetime": {}}},
                                        "timestamp": {"selector": {"number": {}}}}},
    "input_button": {"press": {}},
    "counter": {"increment": {}, "decrement": {}, "reset": {},
                "set_value": {"value": {"required": True, "selector": {"number": {}}}}},
    "timer": {"start": {"duration": {"selector": {"duration": {}}}}, "pause": {}, "cancel": {}, "finish": {},
              "change": {"duration": {"required": True, "selector": {"duration": {}}}}},
}

#: What each domain's configuration takes, for an editor (kinds as in the automation catalog).
FIELDS: dict[str, list[dict[str, Any]]] = {
    "input_boolean": [{"name": "initial", "label": "Starts as on", "kind": "boolean"}],
    "input_number": [
        {"name": "min", "label": "Minimum", "kind": "number", "required": True, "default": 0},
        {"name": "max", "label": "Maximum", "kind": "number", "required": True, "default": 100},
        {"name": "step", "label": "Step", "kind": "number", "default": 1},
        {"name": "mode", "label": "Shown as", "kind": "select", "options": ["slider", "box"], "default": "slider"},
        {"name": "unit_of_measurement", "label": "Unit", "kind": "text"},
        {"name": "initial", "label": "Starting value", "kind": "number"},
    ],
    "input_text": [
        {"name": "min", "label": "Shortest", "kind": "number", "default": 0},
        {"name": "max", "label": "Longest", "kind": "number", "default": 100},
        {"name": "mode", "label": "Shown as", "kind": "select", "options": ["text", "password"], "default": "text"},
        {"name": "pattern", "label": "Pattern (regular expression)", "kind": "text"},
        {"name": "initial", "label": "Starting text", "kind": "text"},
    ],
    "input_select": [
        {"name": "options", "label": "Options", "kind": "options", "required": True},
        {"name": "initial", "label": "Starting option", "kind": "text"},
    ],
    "input_datetime": [
        {"name": "has_date", "label": "Has a date", "kind": "boolean", "default": True},
        {"name": "has_time", "label": "Has a time", "kind": "boolean", "default": True},
        {"name": "initial", "label": "Starting value", "kind": "text"},
    ],
    "input_button": [],
    "counter": [
        {"name": "initial", "label": "Starting value", "kind": "number", "default": 0},
        {"name": "step", "label": "Step", "kind": "number", "default": 1},
        {"name": "minimum", "label": "Minimum", "kind": "number"},
        {"name": "maximum", "label": "Maximum", "kind": "number"},
        {"name": "restore", "label": "Keep the count across restarts", "kind": "boolean", "default": True},
    ],
    "timer": [
        {"name": "duration", "label": "Duration (HH:MM:SS)", "kind": "duration", "default": "0:00:00"},
        {"name": "restore", "label": "Keep running across restarts", "kind": "boolean", "default": False},
    ],
}

LABELS = {"input_boolean": "Toggle", "input_number": "Number", "input_text": "Text", "input_select": "Dropdown",
          "input_datetime": "Date and time", "input_button": "Button", "counter": "Counter", "timer": "Timer"}

_ID = re.compile(r"^[a-z0-9_]+$")


class HelperError(HubError):
    """A helper configuration or call the hub refuses; ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _slug(text: str) -> str:
    from services.homehub.automation import slugify

    return slugify(text)


def _duration(value: Any) -> timedelta:
    from services.homehub.automation import parse_duration

    try:
        td = parse_duration(value)
    except (TypeError, ValueError) as exc:
        raise HelperError(f"not a duration: {value!r}") from exc
    return td


def fmt_duration(td: timedelta) -> str:
    """HA's ``str(timedelta)`` with whole seconds: ``0:05:00``."""
    secs = max(0, round(td.total_seconds()))
    return str(timedelta(seconds=secs))


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise HelperError(f"{name} must be a number")
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise HelperError(f"{name} must be a number") from None
    if out != out or out in (float("inf"), float("-inf")):
        raise HelperError(f"{name} must be a number")
    return out


def _integer(value: Any, name: str) -> int:
    num = _number(value, name)
    if num != int(num):
        raise HelperError(f"{name} must be a whole number")
    return int(num)


def _bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise HelperError(f"{name} must be true or false")


def _num_text(value: float) -> str:
    """HA writes an input_number's state as a float (``"5.0"``)."""
    return str(float(value))


# -- configuration ---------------------------------------------------------------------------------

def validate_config(domain: str, raw: Any) -> dict[str, Any]:
    """HA's storage item for ``domain`` from ``raw``, checked; raises HelperError naming what is wrong.

    The result carries only the fields HA keeps for that domain (plus ``id`` when ``raw`` had one).
    """
    if domain not in DOMAINS:
        raise HelperError(f"unknown helper type {domain!r}", 404)
    if not isinstance(raw, dict):
        raise HelperError("a helper's configuration must be an object")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise HelperError("a helper needs a name")
    out: dict[str, Any] = {"name": name}
    if raw.get("id") is not None:
        out["id"] = str(raw["id"])
    icon = raw.get("icon")
    if icon not in (None, ""):
        if not isinstance(icon, str):
            raise HelperError("icon must be text such as mdi:timer")
        out["icon"] = icon
    initial = raw.get("initial")
    has_initial = initial not in (None, "")

    if domain == "input_boolean":
        if has_initial:
            out["initial"] = _bool(initial, "initial")
    elif domain == "input_number":
        lo, hi = _number(raw.get("min", 0), "min"), _number(raw.get("max", 100), "max")
        if lo >= hi:
            raise HelperError("max must be greater than min")
        step = _number(raw.get("step", 1), "step")
        if step <= 0:
            raise HelperError("step must be greater than 0")
        mode = str(raw.get("mode") or "slider")
        if mode not in ("slider", "box"):
            raise HelperError("mode must be slider or box")
        out.update({"min": lo, "max": hi, "step": step, "mode": mode})
        unit = raw.get("unit_of_measurement")
        if unit not in (None, ""):
            out["unit_of_measurement"] = str(unit)
        if has_initial:
            value = _number(initial, "initial")
            if not lo <= value <= hi:
                raise HelperError(f"initial must be between {lo:g} and {hi:g}")
            out["initial"] = value
    elif domain == "input_text":
        lo, hi = _integer(raw.get("min", 0), "min"), _integer(raw.get("max", 100), "max")
        if lo < 0 or hi > 255 or lo > hi:
            raise HelperError("min and max must be from 0 to 255, min no more than max")
        mode = str(raw.get("mode") or "text")
        if mode not in ("text", "password"):
            raise HelperError("mode must be text or password")
        out.update({"min": lo, "max": hi, "mode": mode})
        pattern = raw.get("pattern")
        if pattern not in (None, ""):
            try:
                re.compile(str(pattern))
            except re.error as exc:
                raise HelperError(f"pattern is not a regular expression: {exc}") from None
            out["pattern"] = str(pattern)
        if has_initial:
            out["initial"] = str(initial)
            _check_text(out, out["initial"])
    elif domain == "input_select":
        options = raw.get("options")
        if not isinstance(options, list) or not options:
            raise HelperError("a dropdown needs at least one option")
        opts = [str(o).strip() for o in options]
        if any(not o for o in opts):
            raise HelperError("an option cannot be empty")
        if len(set(opts)) != len(opts):
            raise HelperError("options must be different from each other")
        out["options"] = opts
        if has_initial:
            if str(initial) not in opts:
                raise HelperError(f"initial {initial!r} is not one of the options")
            out["initial"] = str(initial)
    elif domain == "input_datetime":
        has_date = _bool(raw.get("has_date", False), "has_date")
        has_time = _bool(raw.get("has_time", False), "has_time")
        if not has_date and not has_time:
            raise HelperError("a date and time helper needs a date, a time, or both")
        out.update({"has_date": has_date, "has_time": has_time})
        if has_initial:
            _parse_datetime_value(str(initial), has_date, has_time)
            out["initial"] = str(initial)
    elif domain == "counter":
        out["initial"] = _integer(raw.get("initial", 0) if has_initial else 0, "initial")
        step = _integer(raw.get("step", 1), "step")
        if step < 1:
            raise HelperError("step must be 1 or more")
        out["step"] = step
        for key in ("minimum", "maximum"):
            if raw.get(key) not in (None, ""):
                out[key] = _integer(raw[key], key)
        if "minimum" in out and "maximum" in out and out["minimum"] > out["maximum"]:
            raise HelperError("maximum must not be below minimum")
        if not _within(out, out["initial"]):
            raise HelperError("initial must be between minimum and maximum")
        out["restore"] = _bool(raw.get("restore", True), "restore")
    elif domain == "timer":
        td = _duration(raw.get("duration", 0))
        if td.total_seconds() < 0:
            raise HelperError("duration cannot be negative")
        out["duration"] = fmt_duration(td)
        out["restore"] = _bool(raw.get("restore", False), "restore")
    return out


def _check_text(conf: dict[str, Any], value: str) -> None:
    if not conf.get("min", 0) <= len(value) <= conf.get("max", 100):
        raise HelperError(f"the text must be {conf.get('min', 0)} to {conf.get('max', 100)} characters long")
    pattern = conf.get("pattern")
    # The whole text must match, as HA's frontend checks it (an HTML pattern), not just a prefix.
    if pattern and not re.fullmatch(pattern, value):
        raise HelperError(f"the text does not match the pattern {pattern}")


def _within(conf: dict[str, Any], value: int) -> bool:
    lo, hi = conf.get("minimum"), conf.get("maximum")
    return (lo is None or value >= lo) and (hi is None or value <= hi)


def _parse_datetime_value(value: str, has_date: bool, has_time: bool) -> tuple[date | None, time | None]:
    """An input_datetime state or ``initial``: ``YYYY-MM-DD HH:MM:SS``, ``YYYY-MM-DD`` or ``HH:MM:SS``."""
    s = value.strip()
    try:
        if has_date and has_time:
            dt = datetime.fromisoformat(s.replace("T", " "))
            return dt.date(), dt.time().replace(microsecond=0, tzinfo=None)
        if has_date:
            return date.fromisoformat(s[:10]), None
        return None, time.fromisoformat(s[-8:] if len(s) > 8 and " " in s else s).replace(microsecond=0)
    except ValueError:
        raise HelperError(f"{value!r} is not a valid {'date and time' if has_date and has_time else 'date' if has_date else 'time'}") from None


# -- runtime state of one timer ------------------------------------------------------------------------

@dataclass
class _Timer:
    status: str = IDLE
    duration: timedelta = timedelta(0)  # the duration this run started with
    finishes_at: datetime | None = None
    remaining: timedelta | None = None
    task: asyncio.Task | None = None


@dataclass
class Helper:
    domain: str
    id: str
    entity_id: str
    config: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return {"domain": self.domain, "id": self.id, "entity_id": self.entity_id, "config": dict(self.config)}


class Helpers:
    """Every helper on the hub; also the platform driver their entity services reach."""

    platform = "helpers"

    def __init__(self, hub: Hub, store: Any = None, *, now: Any = None) -> None:
        self.hub = hub
        self.store = store
        self.now = now or (lambda: datetime.now(UTC))
        self.helpers: dict[str, Helper] = {}  # entity_id -> helper
        self.problems: list[str] = []
        self._timers: dict[str, _Timer] = {}
        self._started = False
        #: Timer events waiting for the automations (see :meth:`hold_events`); None when they go out at once.
        self._held: list[tuple[str, dict[str, Any]]] | None = None
        try:
            self.tz = ZoneInfo(getattr(hub, "time_zone", None) or "UTC")
        except Exception:
            self.tz = ZoneInfo("UTC")

    # -- store ------------------------------------------------------------------------------------
    def _stored(self) -> dict[str, list[dict[str, Any]]]:
        if self.store is None:
            return {}
        raw = self.store.get_kv(STORE_KEY, {}) or {}
        return {d: [dict(i) for i in raw.get(d) or [] if isinstance(i, dict)] for d in DOMAINS}

    def _write(self, stored: dict[str, list[dict[str, Any]]]) -> None:
        if self.store is not None:
            self.store.set_kv(STORE_KEY, {d: stored.get(d) or [] for d in DOMAINS})

    # -- lifecycle --------------------------------------------------------------------------------
    async def start(self) -> None:
        """Register the services, then every stored helper with its restored value."""
        for domain in DOMAINS:
            self.hub.platform_drivers[domain] = self
            for service, fields in SERVICES[domain].items():
                self.hub.service_schemas[(domain, service)] = fields
            self.hub.register_service(domain, "reload", self._reload_service)
        self._started = True
        await self.reload()

    async def stop(self) -> None:
        self._started = False
        for t in self._timers.values():
            self._cancel_task(t)
        self._timers.clear()
        for domain in DOMAINS:
            if self.hub.platform_drivers.get(domain) is self:
                del self.hub.platform_drivers[domain]

    async def _reload_service(self, _hub: Hub, _call: ServiceCall) -> None:
        await self.reload()

    async def reload(self) -> None:
        """(Re)build every helper from the store, keeping the values they have now."""
        self.problems = []
        seen: set[str] = set()
        for domain, items in self._stored().items():
            for raw in items:
                try:
                    conf = validate_config(domain, raw)
                    hid = str(raw.get("id") or "")
                    if not _ID.match(hid):
                        raise HelperError(f"id {hid!r} is not a slug")
                    conf["id"] = hid
                except HelperError as exc:
                    label = raw.get("name") or raw.get("id") or "a helper"
                    self.problems.append(f"{domain} {label}: {exc}")
                    log.error("helper %s %s left out: %s", domain, label, exc)
                    continue
                helper = self._install(domain, conf)
                seen.add(helper.entity_id)
        for eid in [e for e in self.helpers if e not in seen]:
            self._remove(eid)

    def _install(self, domain: str, conf: dict[str, Any]) -> Helper:
        ent = self._registry_entry(domain, conf["id"], conf["name"])
        old = self.helpers.get(ent.entity_id)
        helper = Helper(domain, conf["id"], ent.entity_id, conf)
        self.helpers[ent.entity_id] = helper
        if not ent.disabled_by:
            self._publish_initial(helper, changed=old is not None and old.config != conf, fresh=old is None)
        return helper

    def _registry_entry(self, domain: str, hid: str, name: str) -> EntityEntry:
        for e in self.hub.entities.values():
            if e.platform == domain and str(e.unique_id) == hid and e.domain == domain:
                if e.original_name != name:
                    e.original_name = name
                    self._save_entity(e)
                return e
        base = f"{domain}.{hid}"
        eid, n = base, 2
        while eid in self.hub.entities or (self.hub.states.get(eid) is not None and eid not in self.helpers):
            eid = f"{base}_{n}"
            n += 1
        ent = EntityEntry(entity_id=eid, platform=domain, unique_id=hid, original_name=name)
        self.hub.entities[eid] = ent
        self._save_entity(ent)
        return ent

    def _save_entity(self, ent: EntityEntry) -> None:
        if self.store is not None and hasattr(self.store, "save_entity"):
            self.store.save_entity(ent)

    def _remove(self, entity_id: str) -> None:
        self.helpers.pop(entity_id, None)
        t = self._timers.pop(entity_id, None)
        if t is not None:
            self._cancel_task(t)
        self.hub.entities.pop(entity_id, None)
        if self.store is not None and hasattr(self.store, "_exec"):
            with contextlib.suppress(Exception):
                self.store._exec("DELETE FROM entities WHERE entity_id=?", (entity_id,))
        self.hub.states.remove(entity_id)

    # -- admin ------------------------------------------------------------------------------------
    def list(self) -> list[dict[str, Any]]:
        out = []
        for h in sorted(self.helpers.values(), key=lambda h: (DOMAINS.index(h.domain), h.config["name"].lower())):
            st = self.hub.states.get(h.entity_id)
            ent = self.hub.entities.get(h.entity_id)
            out.append({**h.public(), "state": st.as_dict() if st else None,
                        "name": self.hub.friendly_name(ent) if ent else h.config["name"],
                        "disabled": bool(ent and ent.disabled_by)})
        return out

    def get(self, domain: str, hid: str) -> Helper:
        for h in self.helpers.values():
            if h.domain == domain and h.id == hid:
                return h
        raise HelperError(f"no {domain} helper {hid!r}", 404)

    async def create(self, domain: str, raw: Any) -> Helper:
        conf = validate_config(domain, raw)
        stored = self._stored()
        taken = {str(i.get("id")) for i in stored[domain]}
        wanted = conf.pop("id", None)
        if wanted is not None:
            if not _ID.match(wanted):
                raise HelperError("id must be lower-case letters, digits and underscores")
            if wanted in taken:
                raise HelperError(f"a {domain} helper with id {wanted} already exists", 409)
            hid = wanted
        else:
            # A registry entry HA left for the same id is reused (_registry_entry), keeping its entity id.
            base = _slug(conf["name"])
            hid, n = base, 2
            while hid in taken:
                hid = f"{base}_{n}"
                n += 1
        stored[domain].append({"id": hid, **conf})
        self._write(stored)
        await self.reload()
        return self.get(domain, hid)

    async def update(self, domain: str, hid: str, raw: Any) -> Helper:
        self.get(domain, hid)
        conf = validate_config(domain, raw)
        conf.pop("id", None)
        stored = self._stored()
        stored[domain] = [{"id": hid, **conf} if str(i.get("id")) == hid else i for i in stored[domain]]
        self._write(stored)
        await self.reload()
        return self.get(domain, hid)

    async def delete(self, domain: str, hid: str) -> None:
        helper = self.get(domain, hid)
        stored = self._stored()
        stored[domain] = [i for i in stored[domain] if str(i.get("id")) != hid]
        self._write(stored)
        self._remove(helper.entity_id)

    # -- states ---------------------------------------------------------------------------------
    def _publish(self, helper: Helper, state: Any, extra: dict[str, Any] | None = None) -> None:
        ent = self.hub.entities.get(helper.entity_id)
        if ent is None or ent.disabled_by:
            return
        attrs = self._attributes(helper)
        attrs.update(extra or {})
        if not ent.icon and helper.config.get("icon"):
            attrs["icon"] = helper.config["icon"]
        self.hub.publish(ent, state, attrs)

    def _attributes(self, helper: Helper) -> dict[str, Any]:
        c = helper.config
        d = helper.domain
        attrs: dict[str, Any] = {"editable": True}
        if d == "input_number":
            attrs.update({"initial": c.get("initial"), "min": c["min"], "max": c["max"], "step": c["step"],
                          "mode": c["mode"]})
            if c.get("unit_of_measurement"):
                attrs["unit_of_measurement"] = c["unit_of_measurement"]
        elif d == "input_text":
            attrs.update({"min": c["min"], "max": c["max"], "pattern": c.get("pattern"), "mode": c["mode"]})
        elif d == "input_select":
            attrs["options"] = list(c["options"])
        elif d == "input_datetime":
            attrs.update({"has_date": c["has_date"], "has_time": c["has_time"]})
        elif d == "counter":
            attrs.update({"initial": c["initial"], "step": c["step"]})
            for k in ("minimum", "maximum"):
                if k in c:
                    attrs[k] = c[k]
        elif d == "timer":
            attrs["duration"] = c["duration"]
            if c.get("restore"):
                attrs["restore"] = True
        return attrs

    def _value(self, helper: Helper) -> str | None:
        st = self.hub.states.get(helper.entity_id)
        return st.state if st is not None else None

    def _publish_initial(self, helper: Helper, *, changed: bool, fresh: bool) -> None:
        """A helper's first state: ``initial``, else its restored value, else HA's default."""
        c, d = helper.config, helper.domain
        prev = self.hub.states.get(helper.entity_id)
        prev_value = prev.state if prev is not None and prev.state not in ("unavailable", UNKNOWN, "") else None
        if d == "timer":
            self._start_timer_state(helper, prev, fresh=fresh)
            return
        use_initial = "initial" in c and (fresh or changed)
        if d == "input_boolean":
            value = ("on" if c["initial"] else "off") if use_initial else (prev_value if prev_value in ("on", "off") else "off")
            self._publish(helper, value)
        elif d == "input_number":
            value = c["initial"] if use_initial else None
            if value is None and prev_value is not None:
                with contextlib.suppress(ValueError):
                    num = float(prev_value)
                    value = num if c["min"] <= num <= c["max"] else None
            self._publish(helper, _num_text(c["min"] if value is None else value))
        elif d == "input_text":
            value = c["initial"] if use_initial else (prev.state if prev is not None and prev.state != "unavailable" else None)
            if value not in (None, UNKNOWN):
                try:
                    _check_text(c, str(value))
                except HelperError:
                    value = None
            self._publish(helper, UNKNOWN if value is None else value)
        elif d == "input_select":
            value = c["initial"] if use_initial else (prev_value if prev_value in c["options"] else None)
            self._publish(helper, value if value is not None else c["options"][0])
        elif d == "input_datetime":
            raw = c["initial"] if use_initial else prev_value
            parsed: tuple[date | None, time | None] | None = None
            if raw is not None:
                with contextlib.suppress(HelperError):
                    parsed = _parse_datetime_value(str(raw), c["has_date"], c["has_time"])
            if parsed is None:
                parsed = (date(1970, 1, 1) if c["has_date"] else None, time(0, 0, 0) if c["has_time"] else None)
            self._set_datetime(helper, *parsed)
        elif d == "input_button":
            self._publish(helper, prev_value or UNKNOWN)
        elif d == "counter":
            value: Any = None
            # A restart restores the count only with restore on; a reload keeps the count it has.
            if prev_value is not None and (not fresh or c.get("restore", True)):
                with contextlib.suppress(ValueError):
                    value = int(float(prev_value))
            if value is None or not _within(c, value):
                value = c["initial"]
            self._publish(helper, str(value))

    def _set_datetime(self, helper: Helper, d: date | None, t: time | None) -> None:
        c = helper.config
        attrs: dict[str, Any] = {}
        if c["has_date"] and c["has_time"] and d and t:
            dt = datetime.combine(d, t)
            state = dt.strftime("%Y-%m-%d %H:%M:%S")
            attrs.update({"year": d.year, "month": d.month, "day": d.day, "hour": t.hour, "minute": t.minute,
                          "second": t.second, "timestamp": dt.replace(tzinfo=self.tz).timestamp()})
        elif c["has_date"] and d:
            state = d.isoformat()
            attrs.update({"year": d.year, "month": d.month, "day": d.day,
                          "timestamp": datetime.combine(d, time(0)).replace(tzinfo=self.tz).timestamp()})
        else:
            t = t or time(0)
            state = t.strftime("%H:%M:%S")
            attrs.update({"hour": t.hour, "minute": t.minute, "second": t.second,
                          "timestamp": t.hour * 3600 + t.minute * 60 + t.second})
        self._publish(helper, state, attrs)

    # -- services ---------------------------------------------------------------------------------
    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        helper = self.helpers.get(entity.entity_id)
        if helper is None:
            raise HubError(f"{entity.entity_id} is not a helper the hub keeps")
        if service not in SERVICES.get(helper.domain, {}):
            raise ServiceNotFound(f"{helper.domain} has no service {service}")
        handler = getattr(self, f"_svc_{helper.domain}")
        return handler(helper, service, data)

    def _svc_input_boolean(self, h: Helper, service: str, data: dict[str, Any]) -> None:
        cur = self._value(h)
        on = {"turn_on": True, "turn_off": False}.get(service, cur != "on")
        self._publish(h, "on" if on else "off")

    def _svc_input_number(self, h: Helper, service: str, data: dict[str, Any]) -> None:
        c = h.config
        cur = float(self._value(h) or c["min"])
        if service == "set_value":
            if "value" not in data:
                raise HelperError("set_value needs a value")
            new = _number(data["value"], "value")
        else:
            new = cur + (c["step"] if service == "increment" else -c["step"])
        if not c["min"] <= new <= c["max"]:
            raise HelperError(f"{new:g} is outside {c['min']:g} to {c['max']:g} for {h.entity_id}")
        self._publish(h, _num_text(new))

    def _svc_input_text(self, h: Helper, service: str, data: dict[str, Any]) -> None:
        if "value" not in data:
            raise HelperError("set_value needs a value")
        value = str(data["value"])
        _check_text(h.config, value)
        self._publish(h, value)

    def _svc_input_select(self, h: Helper, service: str, data: dict[str, Any]) -> None:
        c = h.config
        opts: list[str] = c["options"]
        cur = self._value(h)
        idx = opts.index(cur) if cur in opts else 0
        if service == "select_option":
            option = str(data.get("option", ""))
            if option not in opts:
                raise HelperError(f"{option!r} is not an option of {h.entity_id}")
            self._publish(h, option)
        elif service in ("select_next", "select_previous"):
            cycle = data.get("cycle", True)
            step = 1 if service == "select_next" else -1
            nxt = idx + step
            if not 0 <= nxt < len(opts):
                nxt = nxt % len(opts) if cycle else idx
            self._publish(h, opts[nxt])
        elif service == "select_first":
            self._publish(h, opts[0])
        elif service == "select_last":
            self._publish(h, opts[-1])
        elif service == "set_options":
            new = data.get("options")
            new = [new] if isinstance(new, str) else new
            conf = validate_config("input_select", {"name": c["name"], "options": new})
            c["options"] = conf["options"]
            self._publish(h, cur if cur in c["options"] else c["options"][0])

    def _svc_input_datetime(self, h: Helper, service: str, data: dict[str, Any]) -> None:
        c = h.config
        cur = self._value(h)
        cur_d, cur_t = (None, None)
        if cur not in (None, UNKNOWN):
            with contextlib.suppress(HelperError):
                cur_d, cur_t = _parse_datetime_value(cur, c["has_date"], c["has_time"])
        given = [k for k in ("date", "time", "datetime", "timestamp") if data.get(k) not in (None, "")]
        if not given:
            raise HelperError("set_datetime needs a date, time, datetime or timestamp")
        if ("datetime" in given or "timestamp" in given) and len(given) > 1:
            raise HelperError("give datetime or timestamp on its own")
        new_d, new_t = cur_d, cur_t
        if "timestamp" in given:
            dt = datetime.fromtimestamp(_number(data["timestamp"], "timestamp"), self.tz)
            new_d, new_t = dt.date(), dt.time().replace(microsecond=0, tzinfo=None)
        elif "datetime" in given:
            try:
                dt = datetime.fromisoformat(str(data["datetime"]).replace("T", " "))
            except ValueError:
                raise HelperError(f"{data['datetime']!r} is not a date and time") from None
            if dt.tzinfo is not None:
                dt = dt.astimezone(self.tz)
            new_d, new_t = dt.date(), dt.time().replace(microsecond=0, tzinfo=None)
        else:
            if "date" in given:
                if not c["has_date"]:
                    raise HelperError(f"{h.entity_id} has no date")
                try:
                    new_d = date.fromisoformat(str(data["date"]))
                except ValueError:
                    raise HelperError(f"{data['date']!r} is not a date") from None
            if "time" in given:
                if not c["has_time"]:
                    raise HelperError(f"{h.entity_id} has no time")
                try:
                    new_t = time.fromisoformat(str(data["time"])).replace(microsecond=0)
                except ValueError:
                    raise HelperError(f"{data['time']!r} is not a time") from None
        self._set_datetime(h, new_d or date(1970, 1, 1), new_t or time(0))

    def _svc_input_button(self, h: Helper, service: str, data: dict[str, Any]) -> None:
        self._publish(h, self.now().astimezone(UTC).isoformat(timespec="microseconds"))

    def _svc_counter(self, h: Helper, service: str, data: dict[str, Any]) -> None:
        c = h.config
        try:
            cur = int(float(self._value(h) or c["initial"]))
        except ValueError:
            cur = c["initial"]
        if service == "set_value":
            if "value" not in data:
                raise HelperError("set_value needs a value")
            new = _integer(data["value"], "value")
            if not _within(c, new):
                raise HelperError(f"{new} is outside the counter's minimum and maximum")
        elif service == "reset":
            new = c["initial"]
        else:
            new = cur + (c["step"] if service == "increment" else -c["step"])
            if "minimum" in c:
                new = max(new, c["minimum"])
            if "maximum" in c:
                new = min(new, c["maximum"])
        self._publish(h, str(new))

    # -- timers -----------------------------------------------------------------------------------
    def _timer(self, h: Helper) -> _Timer:
        return self._timers.setdefault(h.entity_id, _Timer())

    def _start_timer_state(self, h: Helper, prev: Any, *, fresh: bool) -> None:
        t = self._timers.get(h.entity_id)
        if t is not None and not fresh:
            self._publish_timer(h)  # a changed configuration keeps a running timer running
            return
        t = self._timer(h)
        if h.config.get("restore") and prev is not None and prev.state in (ACTIVE, PAUSED):
            a = prev.attributes
            with contextlib.suppress(ValueError, TypeError, HelperError):
                t.duration = _duration(a.get("duration") or h.config["duration"])
                if prev.state == ACTIVE and a.get("finishes_at"):
                    t.status, t.finishes_at = ACTIVE, datetime.fromisoformat(str(a["finishes_at"]))
                    if t.finishes_at <= self.now():
                        self._finish(h)  # ran out while the hub was down: HA finishes it on restore
                        return
                    self._publish_timer(h)
                    self._schedule(h)
                    return
                if prev.state == PAUSED and a.get("remaining"):
                    t.status, t.remaining = PAUSED, _duration(a["remaining"])
                    self._publish_timer(h)
                    return
        t.status, t.finishes_at, t.remaining = IDLE, None, None
        self._publish_timer(h)

    def _publish_timer(self, h: Helper) -> None:
        t = self._timer(h)
        extra: dict[str, Any] = {}
        if t.status == ACTIVE and t.finishes_at is not None:
            extra["duration"] = fmt_duration(t.duration)
            extra["finishes_at"] = t.finishes_at.isoformat()
            extra["remaining"] = fmt_duration(t.finishes_at - self.now())
        elif t.status == PAUSED and t.remaining is not None:
            extra["duration"] = fmt_duration(t.duration)
            extra["remaining"] = fmt_duration(t.remaining)
        self._publish(h, t.status, extra)

    def hold_events(self) -> None:
        """Keep timer events back until :meth:`release_events`.

        The hub starts the helpers before the automations (which name them), and the automations attach only
        once the integrations have loaded. A timer that ran out while the hub was down finishes on restore, so
        its ``timer.finished`` would reach nobody; held, it reaches the automations that wait for it.
        """
        if self._held is None:
            self._held = []

    def release_events(self) -> None:
        """Send the held timer events, in the order they happened, and send later ones at once."""
        held, self._held = self._held or [], None
        for event_type, data in held:
            self.hub.bus.fire(event_type, data)

    def _fire(self, event: str, h: Helper, **extra: Any) -> None:
        data = {"entity_id": h.entity_id, **extra}
        if self._held is not None:
            self._held.append((f"timer.{event}", data))
            return
        self.hub.bus.fire(f"timer.{event}", data)

    def _cancel_task(self, t: _Timer) -> None:
        if t.task is not None and not t.task.done():
            t.task.cancel()
        t.task = None

    def _schedule(self, h: Helper) -> None:
        t = self._timer(h)
        self._cancel_task(t)
        if not self._started:
            return
        with contextlib.suppress(RuntimeError):  # no running loop: run_due() is the only way then
            t.task = asyncio.get_running_loop().create_task(self._wait(h.entity_id), name=f"timer:{h.entity_id}")

    async def _wait(self, entity_id: str) -> None:
        while True:
            h, t = self.helpers.get(entity_id), self._timers.get(entity_id)
            if h is None or t is None or t.status != ACTIVE or t.finishes_at is None:
                return
            delay = (t.finishes_at - self.now()).total_seconds()
            if delay <= 0:
                t.task = None
                self._finish(h)
                return
            await asyncio.sleep(min(delay, MAX_SLEEP_S))

    def run_due(self) -> None:
        """Finish every active timer whose time is up (the timer tasks do this; tests with a fake clock call it)."""
        now = self.now()
        for eid, t in list(self._timers.items()):
            h = self.helpers.get(eid)
            if h is not None and t.status == ACTIVE and t.finishes_at is not None and t.finishes_at <= now:
                self._cancel_task(t)
                self._finish(h)

    def _finish(self, h: Helper) -> None:
        t = self._timer(h)
        self._cancel_task(t)
        t.status, t.finishes_at, t.remaining = IDLE, None, None
        self._publish_timer(h)
        self._fire("finished", h, finished_at=self.now().isoformat())

    def _svc_timer(self, h: Helper, service: str, data: dict[str, Any]) -> None:
        t = self._timer(h)
        now = self.now()
        if service == "start":
            given = data.get("duration")
            if given not in (None, ""):
                duration = _duration(given)
                if duration.total_seconds() <= 0:
                    raise HelperError("duration must be more than zero")
            elif t.status == PAUSED and t.remaining is not None:
                duration = t.remaining
            else:
                duration = _duration(h.config["duration"])
                if duration.total_seconds() <= 0:
                    raise HelperError(f"{h.entity_id} has no duration; give one")
            event = "restarted" if t.status == ACTIVE else "started"
            if given in (None, "") and t.status == PAUSED:
                t.finishes_at = now + duration  # resume: the run keeps its original duration
            else:
                t.duration, t.finishes_at = duration, now + duration
            t.status, t.remaining = ACTIVE, None
            self._publish_timer(h)
            self._fire(event, h)
            self._schedule(h)
        elif service == "pause":
            if t.status != ACTIVE or t.finishes_at is None:
                return
            self._cancel_task(t)
            t.status, t.remaining, t.finishes_at = PAUSED, max(t.finishes_at - now, timedelta(0)), None
            self._publish_timer(h)
            self._fire("paused", h)
        elif service == "cancel":
            if t.status == IDLE:
                return
            self._cancel_task(t)
            t.status, t.finishes_at, t.remaining = IDLE, None, None
            self._publish_timer(h)
            self._fire("cancelled", h)
        elif service == "finish":
            if t.status == IDLE:
                return
            self._finish(h)
        elif service == "change":
            if t.status not in (ACTIVE, PAUSED):
                raise HelperError(f"{h.entity_id} is not running")
            by = _duration(data.get("duration"))
            left = (t.finishes_at - now) if t.status == ACTIVE and t.finishes_at else (t.remaining or timedelta(0))
            new_left = left + by
            if new_left > t.duration:
                raise HelperError(f"{h.entity_id} cannot run longer than its duration {fmt_duration(t.duration)}")
            if new_left.total_seconds() <= 0:
                self._finish(h)
                return
            if t.status == ACTIVE:
                t.finishes_at = now + new_left
                self._schedule(h)
            else:
                t.remaining = new_left
            self._publish_timer(h)
            self._fire("changed", h)


# -- HA's storage files ----------------------------------------------------------------------------------

def from_ha_storage(config_dir: str) -> dict[str, list[dict[str, Any]]] | None:
    """HA's helpers from ``<config>/.storage/<domain>``; None when HA kept none of them there."""
    import json
    import os

    out: dict[str, list[dict[str, Any]]] = {}
    found = False
    for domain in DOMAINS:
        path = os.path.join(config_dir, ".storage", domain)
        if not os.path.exists(path):
            continue
        found = True
        with open(path, encoding="utf-8") as f:
            data = json.load(f).get("data") or {}
        out[domain] = [copy.deepcopy(i) for i in data.get("items") or [] if isinstance(i, dict)]
    return out if found else None
