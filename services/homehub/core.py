"""The hub's core: states, events, the registry and service dispatch.

Shapes follow Home Assistant's own (state dicts, ``state_changed`` events,
service calls with ``entity_id``/``target``), because every Jarvis caller
already speaks that dialect and the point of the hub is that they keep working
when Home Assistant is gone.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("homehub")

UNAVAILABLE = "unavailable"
UNKNOWN = "unknown"

# Entity services a domain answers. A driver may support fewer; the hub only
# refuses a service no driver of that domain could ever handle.
ON_OFF_DOMAINS = {"light", "switch", "fan", "media_player", "remote", "siren", "input_boolean",
                  "automation", "script", "climate", "humidifier", "vacuum", "water_heater"}


#: Registry fields that say what an entity is or can do. They are the device's to tell: a driver (or the
#: flow that probed the device to add it) sets them through :meth:`Hub.confirm_entity` or by publishing a
#: state that carries them. A value that only the Home Assistant import supplied is a hint, never trusted
#: for capabilities, and is reported as ``imported`` (``EntityEntry.info_source``).
ENTITY_INFO_FIELDS = ("original_name", "original_device_class", "unit_of_measurement", "capabilities",
                      "supported_features", "entity_category", "original_icon", "translation_key")
#: The same for a device: what it is (maker, model, firmware, serial) and how it is reached (MAC, ids).
DEVICE_INFO_FIELDS = ("manufacturer", "model", "model_id", "sw_version", "hw_version", "serial_number",
                      "connections", "identifiers", "configuration_url")
#: Pseudo-field in ``EntityEntry.confirmed``: a driver has published this entity's state (so the state's
#: attributes, and a last state restored from the hub's own store, are the device's own).
STATE_CONFIRMED = "state"
#: The state attributes HA keeps in the entity registry's ``capabilities``, per domain. A driver publishing
#: them is the device telling the hub; ``Hub.publish`` copies them into the registry as confirmed.
CAPABILITY_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "light": ("supported_color_modes", "min_color_temp_kelvin", "max_color_temp_kelvin", "min_mireds",
              "max_mireds", "effect_list"),
    "climate": ("hvac_modes", "min_temp", "max_temp", "target_temp_step", "fan_modes", "preset_modes",
                "swing_modes", "min_humidity", "max_humidity"),
    "water_heater": ("min_temp", "max_temp", "operation_list"),
    "humidifier": ("min_humidity", "max_humidity", "available_modes"),
    "media_player": ("source_list", "sound_mode_list"),
    "number": ("min", "max", "step", "mode"),
    "select": ("options",),
    "sensor": ("state_class", "options"),
    "fan": ("preset_modes",),
    "vacuum": ("fan_speed_list",),
    "event": ("event_types",),
    "remote": ("activity_list",),
    "siren": ("available_tones",),
    "text": ("min", "max", "pattern", "mode"),
}


def _has_value(value: Any) -> bool:
    return value not in (None, "", [], {}, 0)


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_context_id() -> str:
    return uuid.uuid4().hex


class HubError(Exception):
    """A service call the hub understood but could not carry out."""

    status = 400


class ServiceNotFound(HubError):
    status = 400


class EntityNotFound(HubError):
    status = 404


class DeviceUnavailable(HubError):
    """The device did not answer. Callers treat this like HA's 'unavailable'."""

    status = 503


@dataclass
class State:
    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    last_changed: datetime = field(default_factory=utcnow)
    last_updated: datetime = field(default_factory=utcnow)
    context_id: str = field(default_factory=new_context_id)

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def object_id(self) -> str:
        return self.entity_id.split(".", 1)[1]

    @property
    def name(self) -> str:
        return str(self.attributes.get("friendly_name") or self.object_id.replace("_", " "))

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "state": self.state,
            "attributes": dict(self.attributes),
            "last_changed": self.last_changed.isoformat(),
            "last_reported": self.last_updated.isoformat(),
            "last_updated": self.last_updated.isoformat(),
            "context": {"id": self.context_id, "parent_id": None, "user_id": None},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> State:
        def ts(v: Any) -> datetime:
            if isinstance(v, datetime):
                return v
            try:
                return datetime.fromisoformat(str(v))
            except (TypeError, ValueError):
                return utcnow()

        return cls(
            entity_id=d["entity_id"],
            state=str(d.get("state", UNKNOWN)),
            attributes=dict(d.get("attributes") or {}),
            last_changed=ts(d.get("last_changed")),
            last_updated=ts(d.get("last_updated") or d.get("last_changed")),
            context_id=(d.get("context") or {}).get("id") or new_context_id(),
        )


Listener = Callable[[str, dict[str, Any]], Any]


class EventBus:
    """Fire-and-forget events. Listeners may be sync or async; one failing never stops the others."""

    def __init__(self) -> None:
        self._listeners: list[tuple[str | None, Listener]] = []

    def subscribe(self, callback: Listener, event_type: str | None = None) -> Callable[[], None]:
        item = (event_type, callback)
        self._listeners.append(item)

        def unsubscribe() -> None:
            if item in self._listeners:
                self._listeners.remove(item)

        return unsubscribe

    def fire(self, event_type: str, data: dict[str, Any]) -> None:
        for wanted, cb in list(self._listeners):
            if wanted is not None and wanted != event_type:
                continue
            try:
                result = cb(event_type, data)
                if inspect.isawaitable(result):
                    task = asyncio.ensure_future(result)
                    task.add_done_callback(_log_task_error)
            except Exception:
                log.exception("event listener failed for %s", event_type)


def _log_task_error(task: asyncio.Future) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("async event listener failed: %r", task.exception())


class StateMachine:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._states: dict[str, State] = {}

    def get(self, entity_id: str) -> State | None:
        return self._states.get(entity_id)

    def all(self, domain: str | None = None) -> list[State]:
        if domain is None:
            return list(self._states.values())
        return [s for s in self._states.values() if s.domain == domain]

    def entity_ids(self) -> list[str]:
        return list(self._states)

    def set(self, entity_id: str, state: Any, attributes: dict[str, Any] | None = None, *,
            force: bool = False, context_id: str | None = None, restore: State | None = None) -> State:
        """Write a state; fires ``state_changed`` only when something changed (or ``force``)."""
        new_s = "" if state is None else str(state)
        attrs = dict(attributes or {})
        old = self._states.get(entity_id)
        if restore is not None:
            self._states[entity_id] = restore
            return restore
        if old is not None and not force and old.state == new_s and old.attributes == attrs:
            return old
        now = utcnow()
        last_changed = old.last_changed if old is not None and old.state == new_s else now
        new = State(entity_id, new_s, attrs, last_changed, now, context_id or new_context_id())
        self._states[entity_id] = new
        self._bus.fire("state_changed", {"entity_id": entity_id, "old_state": old, "new_state": new})
        return new

    def remove(self, entity_id: str) -> None:
        old = self._states.pop(entity_id, None)
        if old is not None:
            self._bus.fire("state_changed", {"entity_id": entity_id, "old_state": old, "new_state": None})


@dataclass
class ConfigEntry:
    entry_id: str
    domain: str
    title: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    source: str = "import"
    disabled_by: str | None = None
    unique_id: str | None = None
    state: str = "not_loaded"  # loaded | setup_error | not_loaded | setup_retry
    reason: str | None = None

    def public(self) -> dict[str, Any]:
        """What HA's config_entries API shows: never the credentials in ``data``."""
        return {
            "entry_id": self.entry_id, "domain": self.domain, "title": self.title, "source": self.source,
            "state": self.state, "reason": self.reason, "disabled_by": self.disabled_by,
            "supports_options": False, "supports_remove_device": False, "supports_unload": True,
            "supports_reconfigure": False, "pref_disable_new_entities": False, "pref_disable_polling": False,
            "supported_subentry_types": {}, "num_subentries": 0,
        }


@dataclass
class DeviceEntry:
    id: str
    name: str | None = None
    name_by_user: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    model_id: str | None = None
    sw_version: str | None = None
    hw_version: str | None = None
    serial_number: str | None = None
    area_id: str | None = None
    config_entries: list[str] = field(default_factory=list)
    identifiers: list[list[str]] = field(default_factory=list)
    connections: list[list[str]] = field(default_factory=list)
    via_device_id: str | None = None
    disabled_by: str | None = None
    entry_type: str | None = None
    labels: list[str] = field(default_factory=list)
    configuration_url: str | None = None
    #: The ``DEVICE_INFO_FIELDS`` the device itself reported (through its driver or the flow that added it).
    confirmed: list[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.name_by_user or self.name or ""

    def info_source(self) -> dict[str, str]:
        """``field -> "device" | "imported"`` for every info field that has a value."""
        return {f: "device" if f in self.confirmed else "imported"
                for f in DEVICE_INFO_FIELDS if _has_value(getattr(self, f))}

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d["primary_config_entry"] = self.config_entries[0] if self.config_entries else None
        d["info_source"] = self.info_source()
        return d


#: The turns a camera's picture can be given (clockwise degrees), for a camera mounted on its side or upside down.
CAMERA_ROTATIONS = (0, 90, 180, 270)


def camera_rotation(entity: EntityEntry) -> int:
    """How far to turn this camera's picture, from its entity options (``{"camera": {"rotation": 90}}``)."""
    value = ((entity.options or {}).get("camera") or {}).get("rotation", 0)
    return int(value) if value in CAMERA_ROTATIONS and not isinstance(value, bool) else 0


@dataclass
class EntityEntry:
    entity_id: str
    platform: str
    unique_id: str = ""
    config_entry_id: str | None = None
    device_id: str | None = None
    area_id: str | None = None
    name: str | None = None
    original_name: str | None = None
    has_entity_name: bool = False
    icon: str | None = None
    original_icon: str | None = None
    device_class: str | None = None
    original_device_class: str | None = None
    unit_of_measurement: str | None = None
    capabilities: dict[str, Any] | None = None
    supported_features: int = 0
    disabled_by: str | None = None
    hidden_by: str | None = None
    entity_category: str | None = None
    aliases: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    translation_key: str | None = None
    #: The ``ENTITY_INFO_FIELDS`` the device itself reported, plus ``STATE_CONFIRMED`` once a driver published
    #: its state. Anything else in those fields came from the Home Assistant import and is only a hint.
    confirmed: list[str] = field(default_factory=list)

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    def trusted(self, name: str) -> Any:
        """An info field's value when the device reported it; ``None`` when only the import did."""
        return getattr(self, name) if name in self.confirmed else None

    @property
    def state_confirmed(self) -> bool:
        return STATE_CONFIRMED in self.confirmed

    def info_source(self) -> dict[str, str]:
        """``field -> "device" | "imported"`` for every info field that has a value."""
        return {f: "device" if f in self.confirmed else "imported"
                for f in ENTITY_INFO_FIELDS if _has_value(getattr(self, f))}

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = self.unique_id
        d["info_source"] = self.info_source()
        return d


@dataclass
class Area:
    area_id: str
    name: str
    floor_id: str | None = None
    aliases: list[str] = field(default_factory=list)
    icon: str | None = None
    labels: list[str] = field(default_factory=list)


@dataclass
class ServiceCall:
    domain: str
    service: str
    data: dict[str, Any]
    entity_ids: list[str]
    context_id: str = field(default_factory=new_context_id)
    return_response: bool = False


ServiceHandler = Callable[["Hub", ServiceCall], Awaitable[Any]]


class Driver:
    """One integration instance (one HA config entry) talking to real devices.

    A driver owns the entities whose ``config_entry_id`` is its entry. It sets
    their states through :meth:`publish` and carries out entity service calls
    in :meth:`async_call`. It must never block the event loop: wrap blocking
    libraries in ``asyncio.to_thread``.
    """

    platform: str = ""
    #: Seconds between :meth:`async_poll` calls; ``None`` means push-only.
    poll_interval: float | None = 30.0

    def __init__(self, hub: Hub, entry: ConfigEntry) -> None:
        self.hub = hub
        self.entry = entry
        self.log = logging.getLogger(f"homehub.{self.platform or entry.domain}")

    @property
    def entities(self) -> list[EntityEntry]:
        return [e for e in self.hub.entities.values()
                if e.config_entry_id == self.entry.entry_id and not e.disabled_by]

    def entity(self, entity_id: str) -> EntityEntry | None:
        e = self.hub.entities.get(entity_id)
        return e if e is not None and e.config_entry_id == self.entry.entry_id else None

    async def async_setup(self) -> None:
        """Connect and publish the first states. Raise to mark the entry ``setup_retry``."""

    async def async_poll(self) -> None:
        """Refresh states (called every ``poll_interval`` seconds)."""

    async def async_unload(self) -> None:
        """Close connections."""

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        raise ServiceNotFound(f"{self.platform} cannot run {domain}.{service} on {entity.entity_id}")

    def publish(self, entity: EntityEntry | str, state: Any, attributes: dict[str, Any] | None = None) -> State:
        return self.hub.publish(entity, state, attributes)

    def confirm_device(self, device: DeviceEntry | EntityEntry | str | None, **info: Any) -> DeviceEntry | None:
        """Record what the device itself reported about itself (maker, model, firmware, serial, MAC...).

        ``device`` is a device, its id, or one of its entities. The values overwrite whatever the import
        held and are marked as the device's own. ``None`` values are skipped (the protocol did not say).
        """
        if isinstance(device, EntityEntry):
            device = device.device_id
        dev_id = device.id if isinstance(device, DeviceEntry) else device
        if not dev_id:
            return None
        return self.hub.confirm_device(str(dev_id), **info)

    def confirm_entity(self, entity: EntityEntry | str, **info: Any) -> EntityEntry | None:
        """Record what the device reported about one entity (device class, unit, feature flags, ranges...)."""
        return self.hub.confirm_entity(entity, **info)

    @staticmethod
    def user_unit(entity: EntityEntry) -> str | None:
        """The display unit a person chose for a sensor (HA's ``options.sensor.unit_of_measurement``), if any.

        A person's choice may come from the import; the unit the integration reported there may not.
        """
        chosen = (entity.options.get("sensor") or {}).get("unit_of_measurement") if entity.options else None
        return str(chosen) if chosen else None

    def mark_unavailable(self, entities: Iterable[EntityEntry] | None = None) -> None:
        for e in entities if entities is not None else self.entities:
            cur = self.hub.states.get(e.entity_id)
            attrs = dict(cur.attributes) if cur else {}
            self.hub.publish(e, UNAVAILABLE, attrs)


class Hub:
    """Everything the API, the drivers and the automation engine share."""

    def __init__(self, *, time_zone: str = "UTC", location: dict[str, Any] | None = None) -> None:
        self.bus = EventBus()
        self.states = StateMachine(self.bus)
        self.config_entries: dict[str, ConfigEntry] = {}
        self.devices: dict[str, DeviceEntry] = {}
        self.entities: dict[str, EntityEntry] = {}
        self.areas: dict[str, Area] = {}
        self.floors: dict[str, dict[str, Any]] = {}
        self.drivers: dict[str, Driver] = {}
        self.services: dict[tuple[str, str], ServiceHandler] = {}
        self.service_schemas: dict[tuple[str, str], dict[str, Any]] = {}
        self.time_zone = time_zone
        self.location = location or {}
        self.started_at = utcnow()
        #: The Store, when the service runs with one (drivers keeping their own data use it).
        self.store: Any = None
        #: HA entity-registry uuid -> entity_id; HA device triggers name entities by that uuid.
        self.entity_registry_ids: dict[str, str] = {}
        #: Entities a driver has published a state for since the hub started.
        self.live: set[str] = set()

    # -- registry -----------------------------------------------------------
    def entity_area_id(self, entity_id: str) -> str | None:
        e = self.entities.get(entity_id)
        if e is None:
            return None
        if e.area_id:
            return e.area_id
        dev = self.devices.get(e.device_id or "")
        return dev.area_id if dev else None

    def area_name(self, entity_id_or_area: str) -> str | None:
        if entity_id_or_area in self.areas:
            return self.areas[entity_id_or_area].name
        area_id = self.entity_area_id(entity_id_or_area)
        if area_id is None and entity_id_or_area in self.devices:
            area_id = self.devices[entity_id_or_area].area_id
        return self.areas[area_id].name if area_id in self.areas else None

    def area_id_by_name(self, name: str) -> str | None:
        if name in self.areas:
            return name
        low = name.strip().lower()
        for a in self.areas.values():
            if a.name.lower() == low or low in (x.lower() for x in a.aliases):
                return a.area_id
        return None

    def area_entities(self, area: str) -> list[str]:
        area_id = self.area_id_by_name(area)
        if area_id is None:
            return []
        return [eid for eid in self.entities if self.entity_area_id(eid) == area_id]

    def device_entities(self, device_id: str) -> list[str]:
        return [e.entity_id for e in self.entities.values() if e.device_id == device_id]

    def friendly_name(self, entity: EntityEntry) -> str:
        """HA's naming: a user's name wins; else device name + the entity's own name."""
        if entity.name:
            return entity.name
        dev = self.devices.get(entity.device_id or "")
        dev_name = dev.display_name if dev else ""
        own = entity.original_name or ""
        if entity.has_entity_name or (dev_name and own and not own.lower().startswith(dev_name.lower())):
            if dev_name and own:
                return f"{dev_name} {own}"
            return dev_name or own or entity.entity_id.split(".", 1)[1].replace("_", " ")
        return own or dev_name or entity.entity_id.split(".", 1)[1].replace("_", " ")

    # -- states -------------------------------------------------------------
    def publish(self, entity: EntityEntry | str, state: Any, attributes: dict[str, Any] | None = None) -> State:
        """Set a state with the registry's naming and metadata applied.

        A live state (anything but ``unavailable``, which only carries the last attributes along) is the
        device speaking: its feature flags, device class, unit and the attributes HA keeps as registry
        ``capabilities`` are written to the registry as confirmed (``confirm_entity``). Registry values are
        then filled in only when the device confirmed them or a person chose them (``device_class``,
        ``icon``); a value only the import held never reaches a state.
        """
        entry = self.entities.get(entity) if isinstance(entity, str) else entity
        entity_id = entity if isinstance(entity, str) else entity.entity_id
        attrs = dict(attributes or {})
        if entry is not None:
            if str(state) != UNAVAILABLE:
                self._confirm_from_state(entry, attrs)
            attrs.setdefault("friendly_name", self.friendly_name(entry))
            device_class = entry.device_class or entry.trusted("original_device_class")
            if device_class:
                attrs.setdefault("device_class", device_class)
            unit = entry.trusted("unit_of_measurement")
            if unit:
                attrs.setdefault("unit_of_measurement", unit)
            if entry.icon:
                attrs.setdefault("icon", entry.icon)
            features = entry.trusted("supported_features")
            if features and "supported_features" not in attrs:
                attrs["supported_features"] = features
            caps = entry.trusted("capabilities") or {}
            if entry.domain == "sensor" and caps.get("state_class") and "state_class" not in attrs:
                attrs["state_class"] = caps["state_class"]
        return self.states.set(entity_id, state, attrs)

    def _confirm_from_state(self, entry: EntityEntry, attrs: dict[str, Any]) -> None:
        info: dict[str, Any] = {}
        if "supported_features" in attrs:
            with contextlib.suppress(TypeError, ValueError):  # a flag value that is not a number says nothing
                info["supported_features"] = int(attrs["supported_features"] or 0)
        if attrs.get("device_class") and attrs.get("device_class") != entry.device_class:
            info["original_device_class"] = attrs["device_class"]
        if attrs.get("unit_of_measurement"):
            info["unit_of_measurement"] = attrs["unit_of_measurement"]
        keys = CAPABILITY_ATTRIBUTES.get(entry.domain, ())
        caps = {k: attrs[k] for k in keys if attrs.get(k) is not None}
        if caps:
            info["capabilities"] = caps
        self.live.add(entry.entity_id)
        self.confirm_entity(entry, _state=True, **info)

    # -- confirmed registry information ---------------------------------------
    def _save(self, kind: str, record: Any) -> None:
        saver = getattr(self.store, f"save_{kind}", None) if self.store is not None else None
        if callable(saver):
            try:
                saver(record)
            except Exception:  # the registry in memory is right; the next confirmation saves it again
                log.exception("saving the %s %s failed", kind, getattr(record, "entity_id", getattr(record, "id", "")))

    def confirm_entity(self, entity: EntityEntry | str, *, _state: bool = False, **info: Any) -> EntityEntry | None:
        """Write what the device reported about an entity into the registry, marked as the device's own.

        ``capabilities`` merges into what the device confirmed before and replaces anything imported.
        Unknown field names raise (a typo must not pass silently). Saved only when something changed.
        """
        ent = self.entities.get(entity) if isinstance(entity, str) else entity
        if ent is None:
            return None
        unknown = sorted(set(info) - set(ENTITY_INFO_FIELDS))
        if unknown:
            raise ValueError(f"not entity info fields: {', '.join(unknown)} (known: {', '.join(ENTITY_INFO_FIELDS)})")
        changed = False
        confirmed = list(ent.confirmed)
        for name, value in info.items():
            if value is None:
                continue
            if name == "capabilities":
                base = dict(ent.capabilities or {}) if name in confirmed else {}
                value = {**base, **dict(value)}
            if name == "supported_features":
                value = int(value or 0)
            if getattr(ent, name) != value:
                setattr(ent, name, value)
                changed = True
            if name not in confirmed:
                confirmed.append(name)
                changed = True
        if _state and STATE_CONFIRMED not in confirmed:
            confirmed.append(STATE_CONFIRMED)
            changed = True
        if changed:
            ent.confirmed = confirmed
            self._save("entity", ent)
        return ent

    def confirm_device(self, device_id: str, **info: Any) -> DeviceEntry | None:
        """Write what a device reported about itself into the registry, marked as the device's own.

        ``connections`` and ``identifiers`` are merged (the device's pairs first): an imported pair stays as
        a hint discovery matches on. Everything else overwrites. ``None`` values are skipped.
        """
        dev = self.devices.get(device_id)
        if dev is None:
            return None
        unknown = sorted(set(info) - set(DEVICE_INFO_FIELDS))
        if unknown:
            raise ValueError(f"not device info fields: {', '.join(unknown)} (known: {', '.join(DEVICE_INFO_FIELDS)})")
        changed = False
        confirmed = list(dev.confirmed)
        for name, value in info.items():
            if value is None or value == "":
                continue
            if name in ("connections", "identifiers"):
                pairs = [[str(a), str(b)] for a, b in value]
                if not pairs:
                    continue
                merged = list(pairs)
                for old in getattr(dev, name) or []:
                    if [str(x) for x in old] not in merged:
                        merged.append(list(old))
                value = merged
            else:
                value = str(value)
            if getattr(dev, name) != value:
                setattr(dev, name, value)
                changed = True
            if name not in confirmed:
                confirmed.append(name)
                changed = True
        if changed:
            dev.confirmed = confirmed
            self._save("device", dev)
            self.bus.fire("device_registry_updated", {"action": "update", "device_id": dev.id})
        return dev

    # -- services -----------------------------------------------------------
    def register_service(self, domain: str, service: str, handler: ServiceHandler,
                         schema: dict[str, Any] | None = None) -> None:
        self.services[(domain, service)] = handler
        self.service_schemas[(domain, service)] = schema or {}

    def resolve_targets(self, data: dict[str, Any]) -> list[str]:
        """``entity_id`` / ``target`` / ``area_id`` / ``device_id`` -> entity ids (HA's rules)."""
        ids: list[str] = []
        sources = [data, data.get("target") or {}]

        def as_list(v: Any) -> list[str]:
            if v is None:
                return []
            if isinstance(v, str):
                return [x.strip() for x in v.split(",") if x.strip()]
            return [str(x) for x in v]

        for src in sources:
            for eid in as_list(src.get("entity_id")):
                if eid == "all":
                    ids.extend(self.states.entity_ids())
                else:
                    ids.append(eid)
            for area in as_list(src.get("area_id")):
                ids.extend(self.area_entities(area))
            for dev in as_list(src.get("device_id")):
                ids.extend(self.device_entities(dev))
        seen: set[str] = set()
        out = []
        for i in ids:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out

    async def call_service(self, domain: str, service: str, data: dict[str, Any] | None = None, *,
                           context_id: str | None = None, return_response: bool = False) -> dict[str, Any]:
        """Run a service. Returns ``{"changed_states": [...], "response": ...}``."""
        data = dict(data or {})
        targets = self.resolve_targets(data)
        call = ServiceCall(domain, service, data, targets, context_id or new_context_id(), return_response)
        changed: list[State] = []

        def note(event_type: str, ev: dict[str, Any]) -> None:
            if ev.get("new_state") is not None:
                changed.append(ev["new_state"])

        unsub = self.bus.subscribe(note, "state_changed")
        response: Any = None
        try:
            self.bus.fire("call_service", {"domain": domain, "service": service, "service_data": data})
            handler = self.services.get((domain, service))
            if handler is not None:
                response = await handler(self, call)
            elif domain == "homeassistant" and service in ("turn_on", "turn_off", "toggle"):
                await self._dispatch_entities(call, by_entity_domain=True)
            else:
                await self._dispatch_entities(call)
        finally:
            unsub()
        return {"changed_states": changed, "response": response}

    async def _dispatch_entities(self, call: ServiceCall, by_entity_domain: bool = False) -> None:
        if not call.entity_ids:
            if not by_entity_domain and call.domain not in ENTITY_SERVICES \
                    and not any(e.domain == call.domain for e in self.entities.values()):
                # Not an entity service either: a device's own action whose device has not connected
                # (ESPHome registers its actions on connect), or a typo. Say so instead of asking for a target.
                raise ServiceNotFound(f"Service {call.domain}.{call.service} not found (no integration provides it "
                                      "right now; a device's own actions appear once the device connects)")
            raise ServiceNotFound(f"{call.domain}.{call.service} needs a target entity")
        data = {k: v for k, v in call.data.items() if k not in ("entity_id", "target", "area_id", "device_id")}
        errors: list[str] = []
        ran = 0
        jobs = []
        for eid in call.entity_ids:
            entry = self.entities.get(eid)
            if entry is None or entry.disabled_by:
                errors.append(f"{eid}: unknown entity")
                continue
            domain = entry.domain if by_entity_domain else call.domain
            if not by_entity_domain and entry.domain != call.domain and call.domain != "homeassistant":
                # HA silently skips entities of another domain; so do we.
                continue
            driver = self.drivers.get(entry.config_entry_id or "")
            if driver is None:
                errors.append(f"{eid}: no driver is running for {entry.platform}")
                continue
            jobs.append((eid, driver.async_call(entry, domain, call.service, dict(data))))
        results = await asyncio.gather(*(j for _, j in jobs), return_exceptions=True)
        for (eid, _), res in zip(jobs, results, strict=True):
            if isinstance(res, BaseException):
                errors.append(f"{eid}: {res}")
            else:
                ran += 1
        if errors and not ran:
            first = next((r for r in results if isinstance(r, HubError)), None)
            exc_type = type(first) if first is not None else HubError
            raise exc_type("; ".join(errors))
        if errors:
            log.warning("%s.%s partly failed: %s", call.domain, call.service, "; ".join(errors))

    def list_services(self) -> list[dict[str, Any]]:
        """``/api/services`` shape: registered services plus the entity services drivers serve."""
        by_domain: dict[str, dict[str, Any]] = {}
        for (domain, service), schema in self.service_schemas.items():
            by_domain.setdefault(domain, {})[service] = {"name": service, "description": "", "fields": schema}
        for e in self.entities.values():
            for service in ENTITY_SERVICES.get(e.domain, ()):
                by_domain.setdefault(e.domain, {}).setdefault(service, {"name": service, "description": "", "fields": {}})
        return [{"domain": d, "services": s} for d, s in sorted(by_domain.items())]


ENTITY_SERVICES: dict[str, tuple[str, ...]] = {
    "light": ("turn_on", "turn_off", "toggle"),
    "switch": ("turn_on", "turn_off", "toggle"),
    "fan": ("turn_on", "turn_off", "toggle", "set_percentage"),
    "cover": ("open_cover", "close_cover", "stop_cover", "toggle", "set_cover_position"),
    "valve": ("open_valve", "close_valve", "toggle"),
    "lock": ("lock", "unlock"),
    "climate": ("set_temperature", "set_hvac_mode", "set_fan_mode", "set_humidity", "turn_on", "turn_off"),
    "media_player": ("turn_on", "turn_off", "toggle", "play_media", "media_play", "media_pause", "media_stop",
                     "media_play_pause", "media_next_track", "media_previous_track", "volume_set", "volume_up",
                     "volume_down", "volume_mute", "select_source", "media_seek", "shuffle_set", "repeat_set",
                     "clear_playlist", "join", "unjoin"),
    "remote": ("turn_on", "turn_off", "send_command"),
    "button": ("press",),
    "number": ("set_value",),
    "select": ("select_option",),
    "siren": ("turn_on", "turn_off"),
    "vacuum": ("start", "pause", "stop", "return_to_base", "locate", "clean_spot", "set_fan_speed",
               "send_command"),
    "time": ("set_value",),
    "todo": ("add_item", "update_item", "remove_item", "remove_completed_items", "get_items"),
    "camera": ("snapshot",),
    "assist_satellite": ("announce",),
}
