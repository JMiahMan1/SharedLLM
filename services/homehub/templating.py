"""Home Assistant's template language on top of the hub.

Jarvis and the household's automations were written against HA's Jinja
dialect: ``states('light.x')``, ``states.light``, ``area_name(...)``,
``| int(0)``, ``as_timestamp(...) | timestamp_custom('%a')`` and so on. This
module rebuilds that dialect in a sandboxed Jinja environment so those
templates keep rendering unchanged.

Two entry points:

* :func:`render` renders to a string (stripped, like HA), which is what
  ``/api/template`` returns.
* :func:`render_complex` walks dicts and lists, renders every string in them
  and turns results that read as Python literals back into numbers, booleans,
  lists and dicts, the way HA's ``render_complex``/``_parse_result`` does for
  service data and variables.
"""
from __future__ import annotations

import contextvars
import json
import logging
import math
import re
import unicodedata
from ast import literal_eval
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import jinja2
from jinja2.sandbox import ImmutableSandboxedEnvironment

from services.homehub.core import UNAVAILABLE, UNKNOWN, State

log = logging.getLogger("homehub.template")

_SENTINEL = object()
_IS_NUMERIC = re.compile(r"^[+-]?(?!0\d)\d*(?:\.\d*)?$")
MAX_OUTPUT = 256 * 1024


class TemplateError(Exception):
    """A template that could not be compiled or rendered."""


def is_template_string(value: str) -> bool:
    return "{%" in value or "{{" in value or "{#" in value


# -- state objects -------------------------------------------------------------

class TemplateState:
    """A read-only view of a :class:`core.State`, shaped like HA's TemplateState."""

    __slots__ = ("_hub", "_state")

    def __init__(self, hub: Any, state: State) -> None:
        self._hub = hub
        self._state = state

    entity_id = property(lambda self: self._state.entity_id)
    state = property(lambda self: self._state.state)
    attributes = property(lambda self: dict(self._state.attributes))
    domain = property(lambda self: self._state.domain)
    object_id = property(lambda self: self._state.object_id)
    name = property(lambda self: self._state.name)
    last_changed = property(lambda self: self._state.last_changed)
    last_updated = property(lambda self: self._state.last_updated)
    last_reported = property(lambda self: self._state.last_updated)
    context = property(lambda self: {"id": self._state.context_id, "parent_id": None, "user_id": None})

    @property
    def state_with_unit(self) -> str:
        unit = self._state.attributes.get("unit_of_measurement")
        return f"{self._state.state} {unit}" if unit else self._state.state

    def as_dict(self) -> dict[str, Any]:
        return self._state.as_dict()

    def __getitem__(self, item: str) -> Any:
        # HA allows state['state'], state['attributes'] and so on.
        if item in ("entity_id", "state", "attributes", "domain", "object_id", "name", "last_changed",
                    "last_updated", "last_reported", "context", "state_with_unit"):
            return getattr(self, item)
        raise KeyError(item)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, TemplateState):
            other = other._state
        if not isinstance(other, State):
            return NotImplemented
        s = self._state
        return (s.entity_id, s.state, s.attributes, s.last_changed) == (
            other.entity_id, other.state, other.attributes, other.last_changed)

    def __hash__(self) -> int:
        return hash((self._state.entity_id, self._state.state, self._state.last_changed))

    def __repr__(self) -> str:
        return f"<template TemplateState(<state {self._state.entity_id}={self._state.state}>)>"

    __str__ = __repr__


def wrap_state(hub: Any, state: State | TemplateState | None) -> TemplateState | None:
    if state is None or isinstance(state, TemplateState):
        return state
    return TemplateState(hub, state)


class DomainStates:
    """``states.light``: iterable, countable, and ``states.light.kitchen`` by attribute."""

    def __init__(self, hub: Any, domain: str) -> None:
        self._hub = hub
        self._domain = domain

    def __getattr__(self, name: str) -> TemplateState | None:
        if name.startswith("_"):
            raise AttributeError(name)
        return wrap_state(self._hub, self._hub.states.get(f"{self._domain}.{name}"))

    def __getitem__(self, name: str) -> TemplateState | None:
        return self.__getattr__(name)

    def _items(self) -> list[State]:
        return sorted(self._hub.states.all(self._domain), key=lambda s: s.entity_id)

    def __iter__(self) -> Iterator[TemplateState]:
        return (TemplateState(self._hub, s) for s in self._items())

    def __len__(self) -> int:
        return len(self._hub.states.all(self._domain))

    def __repr__(self) -> str:
        return f"<template DomainStates('{self._domain}')>"


class AllStates:
    """``states``: callable (``states('x.y')``), iterable, and ``states.domain``/``states['x.y']``."""

    def __init__(self, hub: Any) -> None:
        self._hub = hub

    def __getattr__(self, name: str) -> DomainStates:
        if name.startswith("_"):
            raise AttributeError(name)
        return DomainStates(self._hub, name)

    def __getitem__(self, key: str) -> Any:
        key = str(key)
        if "." in key:
            return wrap_state(self._hub, self._hub.states.get(key))
        return DomainStates(self._hub, key)

    def __iter__(self) -> Iterator[TemplateState]:
        return (TemplateState(self._hub, s)
                for s in sorted(self._hub.states.all(), key=lambda s: s.entity_id))

    def __len__(self) -> int:
        return len(self._hub.states.all())

    def __call__(self, entity_id: Any, rounded: bool = False, with_unit: bool = False) -> str:
        st = _get_state(self._hub, entity_id)
        if st is None:
            return UNKNOWN
        if with_unit:
            unit = st.attributes.get("unit_of_measurement")
            return f"{st.state} {unit}" if unit else st.state
        return st.state

    def __repr__(self) -> str:
        return "<template AllStates>"


def _get_state(hub: Any, entity_id: Any) -> State | None:
    if isinstance(entity_id, TemplateState):
        return entity_id._state
    if isinstance(entity_id, State):
        return entity_id
    if not isinstance(entity_id, str):
        return None
    return hub.states.get(entity_id.strip().lower())


# -- conversions HA ships as filters and globals ---------------------------------

def _fail(name: str, value: Any) -> None:
    raise ValueError(f"Template error: {name} got invalid input '{value}' when rendering template "
                     f"and no default was specified")


def forgiving_float(value: Any, default: Any = _SENTINEL) -> Any:
    try:
        return float(value)
    except (ValueError, TypeError):
        if default is _SENTINEL:
            _fail("float", value)
        return default


def forgiving_int(value: Any, default: Any = _SENTINEL, base: int = 10) -> Any:
    try:
        if isinstance(value, str) and base != 10:
            return int(value, base)
        return int(float(value)) if not isinstance(value, bool) else int(value)
    except (ValueError, TypeError, OverflowError):
        if default is _SENTINEL:
            _fail("int", value)
        return default


_TRUE = {"1", "true", "yes", "on", "enable"}
_FALSE = {"0", "false", "no", "off", "disable"}


def forgiving_boolean(value: Any, default: Any = _SENTINEL) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        low = value.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
    if default is _SENTINEL:
        _fail("bool", value)
    return default


def forgiving_round(value: Any, precision: int = 0, method: str = "common", default: Any = _SENTINEL) -> Any:
    try:
        v = float(value)
        mult = 10 ** precision
        if method == "ceil":
            out = math.ceil(v * mult) / mult
        elif method == "floor":
            out = math.floor(v * mult) / mult
        elif method == "half":
            out = round(v * 2) / 2
        else:
            # "common" rounds half away from zero, not Python's banker's rounding.
            out = math.floor(abs(v) * mult + 0.5) / mult * (1 if v >= 0 else -1)
        return int(out) if precision == 0 else out
    except (ValueError, TypeError):
        if default is _SENTINEL:
            _fail("round", value)
        return default


def is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        f = float(value)
    except (ValueError, TypeError):
        return False
    return math.isfinite(f)


def iif(value: Any, if_true: Any = True, if_false: Any = False, if_none: Any = _SENTINEL) -> Any:
    if value is None and if_none is not _SENTINEL:
        return if_none
    if value is None:
        return if_false
    return if_true if bool(value) else if_false


def slugify(text: Any, separator: str = "_") -> str:
    """HA's slugify: ASCII-folded, lower case, runs of other characters become one separator."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", separator, s).strip(separator)
    return s or ("unknown" if str(text) else "")


def regex_match(value: Any, find: str = "", ignorecase: bool = False) -> bool:
    return bool(re.match(find, str(value), re.IGNORECASE if ignorecase else 0))


def regex_search(value: Any, find: str = "", ignorecase: bool = False) -> bool:
    return bool(re.search(find, str(value), re.IGNORECASE if ignorecase else 0))


def regex_replace(value: Any = "", find: str = "", replace: str = "", ignorecase: bool = False) -> str:
    return re.sub(find, replace, str(value), flags=re.IGNORECASE if ignorecase else 0)


def regex_findall(value: Any, find: str = "", ignorecase: bool = False) -> list[Any]:
    return re.findall(find, str(value), re.IGNORECASE if ignorecase else 0)


def regex_findall_index(value: Any, find: str = "", index: int = 0, ignorecase: bool = False) -> Any:
    return regex_findall(value, find, ignorecase)[index]


def from_json(value: Any, default: Any = _SENTINEL) -> Any:
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        if default is _SENTINEL:
            raise
        return default


def to_json(value: Any, ensure_ascii: bool = False, pretty_print: bool = False, sort_keys: bool = False) -> str:
    return json.dumps(value, ensure_ascii=ensure_ascii, indent=2 if pretty_print else None,
                      sort_keys=sort_keys, default=_json_default)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, TemplateState):
        return obj.as_dict()
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    if isinstance(obj, (set, tuple)):
        return list(obj)
    return str(obj)


def _contains(seq: Any, value: Any) -> bool:
    try:
        return value in seq
    except TypeError:
        return False


# -- the environment --------------------------------------------------------------

class _Env(ImmutableSandboxedEnvironment):
    """HA's environment: immutable sandbox, plus HA's names (bound per render)."""

    def is_safe_attribute(self, obj: Any, attr: str, value: Any) -> bool:
        if isinstance(obj, (TemplateState, AllStates, DomainStates)):
            return not attr.startswith("_")
        return super().is_safe_attribute(obj, attr, value)


@lru_cache(maxsize=1)
def _env() -> _Env:
    env = _Env(undefined=jinja2.Undefined, extensions=["jinja2.ext.loopcontrols", "jinja2.ext.do"])
    env.filters.update({
        "float": forgiving_float, "int": forgiving_int, "bool": forgiving_boolean, "round": forgiving_round,
        "iif": iif, "from_json": from_json, "to_json": to_json, "regex_match": regex_match,
        "regex_search": regex_search, "regex_replace": regex_replace, "regex_findall": regex_findall,
        "regex_findall_index": regex_findall_index, "slugify": slugify, "is_number": is_number,
        "contains": _contains, "multiply": lambda v, a, d=_SENTINEL: _num_op(v, a, d, lambda x, y: x * y),
        "add": lambda v, a, d=_SENTINEL: _num_op(v, a, d, lambda x, y: x + y),
        "base64_encode": lambda v: __import__("base64").b64encode(str(v).encode()).decode(),
        "base64_decode": lambda v: __import__("base64").b64decode(str(v)).decode(),
        "ord": ord, "md5": lambda v: __import__("hashlib").md5(str(v).encode()).hexdigest(),
        "sha256": lambda v: __import__("hashlib").sha256(str(v).encode()).hexdigest(),
    })
    env.globals.update({
        "float": forgiving_float, "int": forgiving_int, "bool": forgiving_boolean, "round": forgiving_round,
        "iif": iif, "is_number": is_number, "min": lambda *a: min(*a), "max": lambda *a: max(*a),
        "timedelta": timedelta, "slugify": slugify, "from_json": from_json, "to_json": to_json,
        "strptime": _strptime, "pi": math.pi, "e": math.e, "log": _log, "sqrt": math.sqrt,
        "zip": zip, "dict": dict, "set": set, "tuple": tuple, "typeof": lambda v: type(v).__name__,
    })
    for name in _FILTER_NAMES:
        env.filters[name] = _bound(name)
    for name in _TEST_NAMES:
        env.tests[name] = _bound(name)
    env.tests.update({
        "match": regex_match, "search": regex_search, "is_number": is_number, "contains": _contains,
        "list": lambda v: isinstance(v, list), "datetime": lambda v: isinstance(v, datetime),
        "string_like": lambda v: isinstance(v, (str, bytes)),
    })
    return env


def _num_op(value: Any, arg: Any, default: Any, op: Callable[[float, float], float]) -> Any:
    try:
        return op(float(value), arg)
    except (ValueError, TypeError):
        if default is _SENTINEL:
            _fail("multiply", value)
        return default


def _log(value: Any, base: float = math.e, default: Any = _SENTINEL) -> Any:
    try:
        return math.log(float(value), float(base))
    except (ValueError, TypeError):
        if default is _SENTINEL:
            _fail("log", value)
        return default


def _strptime(value: str, fmt: str, default: Any = _SENTINEL) -> Any:
    try:
        return datetime.strptime(value, fmt)
    except (ValueError, TypeError):
        if default is _SENTINEL:
            _fail("strptime", value)
        return default


@lru_cache(maxsize=512)
def _compile(template: str) -> jinja2.Template:
    try:
        return _env().from_string(template)
    except jinja2.TemplateSyntaxError as exc:
        raise TemplateError(f"TemplateSyntaxError: {exc}") from exc


# -- hub-bound helpers --------------------------------------------------------------

class _HubFunctions:
    """The functions that need the hub (and the hub's time zone)."""

    def __init__(self, hub: Any, now: Callable[[], datetime] | None) -> None:
        self.hub = hub
        self._now = now
        try:
            self.tz = ZoneInfo(getattr(hub, "time_zone", None) or "UTC")
        except Exception:
            self.tz = ZoneInfo("UTC")

    # time ------------------------------------------------------------------
    def utcnow(self) -> datetime:
        n = self._now() if self._now else datetime.now(UTC)
        return n.astimezone(UTC)

    def now(self) -> datetime:
        return self.utcnow().astimezone(self.tz)

    def as_local(self, value: Any) -> Any:
        dt = value if isinstance(value, datetime) else self.as_datetime(value)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(self.tz)

    def as_datetime(self, value: Any, default: Any = _SENTINEL) -> Any:
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, time(), tzinfo=self.tz)
        try:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return datetime.fromtimestamp(value, UTC)
            s = str(value).strip()
            if is_number(s):
                return datetime.fromtimestamp(float(s), UTC)
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (ValueError, TypeError, OverflowError):
            if default is _SENTINEL:
                return None
            return default

    def as_timestamp(self, value: Any, default: Any = _SENTINEL) -> Any:
        if isinstance(value, TemplateState):
            value = value.state
        dt = value if isinstance(value, datetime) else self.as_datetime(value)
        if dt is None:
            if default is _SENTINEL:
                _fail("as_timestamp", value)
            return default
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.tz)
        return dt.timestamp()

    def timestamp_custom(self, value: Any, fmt: str = "%Y-%m-%d %H:%M:%S", local: bool = True,
                         default: Any = _SENTINEL) -> Any:
        try:
            dt = datetime.fromtimestamp(float(value), UTC)
        except (ValueError, TypeError, OverflowError):
            if default is _SENTINEL:
                _fail("timestamp_custom", value)
            return default
        return (dt.astimezone(self.tz) if local else dt).strftime(fmt)

    def timestamp_local(self, value: Any, default: Any = _SENTINEL) -> Any:
        try:
            return datetime.fromtimestamp(float(value), UTC).astimezone(self.tz).isoformat()
        except (ValueError, TypeError, OverflowError):
            if default is _SENTINEL:
                _fail("timestamp_local", value)
            return default

    def timestamp_utc(self, value: Any, default: Any = _SENTINEL) -> Any:
        try:
            return datetime.fromtimestamp(float(value), UTC).isoformat()
        except (ValueError, TypeError, OverflowError):
            if default is _SENTINEL:
                _fail("timestamp_utc", value)
            return default

    def today_at(self, value: str = "00:00") -> datetime:
        t = time.fromisoformat(str(value).strip())
        today = self.now().date()
        return datetime.combine(today, t, tzinfo=self.tz)

    def relative_time(self, value: Any) -> Any:
        if not isinstance(value, datetime):
            return value
        if value.tzinfo is None:
            value = value.replace(tzinfo=self.tz)
        delta = self.utcnow() - value
        if delta.total_seconds() < 0:
            return value
        return _humanize(delta)

    # states ----------------------------------------------------------------
    def is_state(self, entity_id: str, value: Any) -> bool:
        st = _get_state(self.hub, entity_id)
        if st is None:
            return False
        if isinstance(value, str):
            return st.state == value
        return st.state in value

    def state_attr(self, entity_id: str, name: str) -> Any:
        st = _get_state(self.hub, entity_id)
        return None if st is None else st.attributes.get(name)

    def is_state_attr(self, entity_id: str, name: str, value: Any) -> bool:
        st = _get_state(self.hub, entity_id)
        return st is not None and name in st.attributes and st.attributes[name] == value

    def has_value(self, entity_id: str) -> bool:
        st = _get_state(self.hub, entity_id)
        return st is not None and st.state not in (UNKNOWN, UNAVAILABLE)

    def expand(self, *args: Any) -> list[TemplateState]:
        found: dict[str, TemplateState] = {}
        queue: list[Any] = list(args)
        seen: set[str] = set()
        while queue:
            item = queue.pop(0)
            if isinstance(item, (list, tuple, set, AllStates, DomainStates)):
                queue.extend(item)
                continue
            st = _get_state(self.hub, item)
            if st is None or st.entity_id in seen:
                continue
            seen.add(st.entity_id)
            members = st.attributes.get("entity_id")
            # Groups (and light/switch groups) list members in ``entity_id``; zones list people, not members.
            if st.domain != "zone" and isinstance(members, (list, tuple)):
                queue.extend(members)
                continue
            found[st.entity_id] = TemplateState(self.hub, st)
        return [found[k] for k in sorted(found)]

    # registry --------------------------------------------------------------
    def area_name(self, lookup: Any) -> str | None:
        return self.hub.area_name(str(lookup)) if lookup else None

    def area_id(self, lookup: Any) -> str | None:
        if not lookup:
            return None
        lookup = str(lookup)
        by_name = self.hub.area_id_by_name(lookup)
        if by_name is not None:
            return by_name
        aid = self.hub.entity_area_id(lookup)
        if aid is None and lookup in self.hub.devices:
            aid = self.hub.devices[lookup].area_id
        return aid

    def area_entities(self, area: Any) -> list[str]:
        return self.hub.area_entities(str(area)) if area else []

    def area_devices(self, area: Any) -> list[str]:
        aid = self.hub.area_id_by_name(str(area)) if area else None
        return [d.id for d in self.hub.devices.values() if aid and d.area_id == aid]

    def areas(self) -> list[str]:
        return list(self.hub.areas)

    def device_id(self, lookup: Any) -> str | None:
        e = self.hub.entities.get(str(lookup))
        if e is not None:
            return e.device_id
        low = str(lookup).lower()
        for d in self.hub.devices.values():
            if d.display_name.lower() == low:
                return d.id
        return None

    def device_attr(self, lookup: Any, attr: str) -> Any:
        dev = self.hub.devices.get(str(lookup))
        if dev is None:
            did = self.device_id(lookup)
            dev = self.hub.devices.get(did or "")
        if dev is None or attr.startswith("_"):
            return None
        return getattr(dev, attr, None)

    def is_device_attr(self, lookup: Any, attr: str, value: Any) -> bool:
        return self.device_attr(lookup, attr) == value

    def device_entities(self, device_id: Any) -> list[str]:
        return self.hub.device_entities(str(device_id))

    def integration_entities(self, integration: str) -> list[str]:
        return [e.entity_id for e in self.hub.entities.values() if e.platform == integration]

    def bind(self) -> dict[str, Any]:
        names = ("utcnow", "now", "as_local", "as_datetime", "as_timestamp", "timestamp_custom", "timestamp_local",
                 "timestamp_utc", "today_at", "relative_time", "is_state", "state_attr", "is_state_attr",
                 "has_value", "expand", "area_name", "area_id", "area_entities", "area_devices", "areas",
                 "device_id", "device_attr", "is_device_attr", "device_entities", "integration_entities")
        return {n: getattr(self, n) for n in names}


_FILTER_NAMES = ("as_local", "as_datetime", "as_timestamp", "timestamp_custom", "timestamp_local", "timestamp_utc",
                 "relative_time", "is_state", "state_attr", "is_state_attr", "has_value", "expand", "area_name",
                 "area_id", "area_entities", "area_devices", "device_id", "device_attr", "is_device_attr",
                 "device_entities", "today_at")
_TEST_NAMES = ("is_state", "is_state_attr", "has_value", "is_device_attr")

# The hub-bound filters and tests are compiled into templates once, so they
# reach the hub of the render in progress through this context variable.
_CURRENT: contextvars.ContextVar[_HubFunctions] = contextvars.ContextVar("homehub_template_hub")


def _bound(name: str) -> Callable[..., Any]:
    def call(*args: Any, **kwargs: Any) -> Any:
        return getattr(_CURRENT.get(), name)(*args, **kwargs)

    call.__name__ = name
    return call


def _humanize(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    for unit, size in (("year", 365 * 86400), ("month", 30 * 86400), ("day", 86400), ("hour", 3600),
                       ("minute", 60)):
        if secs >= size:
            n = round(secs / size) if unit != "year" else secs // size
            return f"{n} {unit}{'s' if n != 1 else ''}"
    return f"{secs} second{'s' if secs != 1 else ''}"


# -- rendering -----------------------------------------------------------------------

def _context(hub: Any, variables: dict[str, Any] | None, now: Callable[[], datetime] | None) -> tuple[
        dict[str, Any], _HubFunctions]:
    fns = _HubFunctions(hub, now)
    ctx = fns.bind()
    ctx["states"] = AllStates(hub)
    if variables:
        ctx.update(variables)
    return ctx, fns


def render(hub: Any, template: str, variables: dict[str, Any] | None = None, *,
           now: Callable[[], datetime] | None = None) -> str:
    """Render to a string, stripped (HA's ``async_render(parse_result=False)``)."""
    if template is None:
        return ""
    template = str(template)
    if not is_template_string(template):
        return template.strip()
    compiled = _compile(template)
    ctx, fns = _context(hub, variables, now)
    token = _CURRENT.set(fns)
    try:
        out = compiled.render(ctx)
    except TemplateError:
        raise
    except jinja2.TemplateError as exc:
        raise TemplateError(f"{type(exc).__name__}: {exc}") from exc
    except Exception as exc:
        raise TemplateError(str(exc) or type(exc).__name__) from exc
    finally:
        _CURRENT.reset(token)
    if len(out) > MAX_OUTPUT:
        raise TemplateError(f"Template output exceeded maximum size of {MAX_OUTPUT} characters")
    return out.strip()


def parse_result(text: str) -> Any:
    """HA's ``_parse_result``: literals become native values; strings stay as rendered."""
    try:
        result = literal_eval(text)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return text
    if isinstance(result, (str, complex)):
        return text
    if isinstance(result, (int, float)) and not isinstance(result, bool) and _IS_NUMERIC.match(text) is None:
        return text
    return result


def render_complex(hub: Any, value: Any, variables: dict[str, Any] | None = None, *,
                   now: Callable[[], datetime] | None = None, parse: bool = True) -> Any:
    """Render every string inside ``value`` (dict keys too), parsing results back to native types."""
    if isinstance(value, dict):
        return {render_complex(hub, k, variables, now=now, parse=parse) if isinstance(k, str) else k:
                render_complex(hub, v, variables, now=now, parse=parse) for k, v in value.items()}
    if isinstance(value, list):
        return [render_complex(hub, v, variables, now=now, parse=parse) for v in value]
    if isinstance(value, str):
        if not is_template_string(value):
            return value
        out = render(hub, value, variables, now=now)
        return parse_result(out) if parse else out
    return value


def render_bool(hub: Any, template: str, variables: dict[str, Any] | None = None, *,
                now: Callable[[], datetime] | None = None) -> bool:
    """A condition template's truth, HA's ``result_as_boolean``."""
    out = render_complex(hub, template, variables, now=now)
    return result_as_boolean(out)


def result_as_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "on", "enable", "1")
    return False


def references_now(template: str) -> bool:
    """Whether a template depends on the clock, so a trigger must re-check it as time passes."""
    return bool(re.search(r"\b(now|utcnow|today_at|relative_time)\s*\(", template))


def iter_templates(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield from iter_templates(k)
            yield from iter_templates(v)
    elif isinstance(value, list):
        for v in value:
            yield from iter_templates(v)
    elif isinstance(value, str) and is_template_string(value):
        yield value
