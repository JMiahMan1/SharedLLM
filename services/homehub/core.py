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
#: HA's UpdateEntityFeature.INSTALL: the integration can really install the latest version.
UPDATE_INSTALL = 1
#: The update services the hub answers itself (a skipped version is the hub's record, not the device's).
UPDATE_SKIP_SERVICES = ("skip", "clear_skipped")
#: How long a combined device's main copy has to carry out a command before the fallback gets it.
COMBINED_MAIN_TIMEOUT_S = 6.0
#: Commands that start playback are not handed to the fallback just because the main is slow: a Cast TV that
#: first switches app and then loads takes longer than 6 s, and the main's worker thread carries on after the
#: wait is given up, so the fallback played the same clip a few seconds after the main did (announcements
#: heard twice). They wait this long, past every media driver's own load timeout, and move on only on an error.
COMBINED_PLAY_SERVICES = frozenset({("media_player", "play_media")})
COMBINED_PLAY_TIMEOUT_S = 40.0

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


class AuthRequired(DeviceUnavailable):
    """The device answered but wants a password the entry does not have, or refused the one it has.

    Raised from a driver's setup or poll, it parks the entry in ``setup_error`` with no retries (HA's
    ``ConfigEntryAuthFailed``): trying a refused password again only locks the account (a Reolink camera locks
    it after a few tries). The entry shows what it needs (``needs``, read by ``entries.needs_of``) until an
    admin enters it, which reloads the entry. ``needs`` is ``"password"`` (the device's own password),
    ``"login"`` (an account's username and password), ``"pairing"`` (pair again: a TV or accessory that
    forgot the hub) or ``"bindkey"`` (a BLE sensor's encryption key).
    """

    def __init__(self, message: str = "", *, needs: str = "password") -> None:
        super().__init__(message)
        self.needs = needs


class SetupError(HubError):
    """A setup that cannot succeed however often it is tried (a required setting is missing or wrong).

    The entry is parked in ``setup_error`` with this reason, with no retries, until an admin changes or
    reloads it (HA's ``ConfigEntryError``). Anything else a setup raises is retried.
    """


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
    #: What a parked entry needs from an admin (``AuthRequired.needs``); None otherwise. Not stored.
    needs: str | None = None

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
    #: ``"user"`` when a person hid the device (a duplicate another integration also sees). A hidden device
    #: keeps working; only the lists people and Jarvis browse leave it out (``set_device_hidden``).
    hidden_by: str | None = None
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
    #: Seconds one :meth:`async_poll` may take before it counts as failed; ``None`` uses the runtime's default.
    poll_timeout: float | None = None
    #: Attributes of this driver's entities that history leaves out (HA's ``_unrecorded_attributes``), on top of
    #: the recorder's per-domain ones (recorder.py): values that churn without meaning anything later, such as a
    #: rotating token or a signal reading. The live state and the restored state keep them.
    unrecorded_attributes: frozenset[str] = frozenset()

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

    def gone_entities(self) -> dict[str, str]:
        """``entity_id -> why`` for this entry's registry entities the integration says no longer exist.

        Not "unavailable" (a device that is off or away comes back): only what the integration's own,
        complete listing no longer has (Music Assistant dropped the player, the ESPHome node's firmware
        no longer has the entity). The prune dry run lists them (``entity_gone``) for a person to review;
        nothing is removed on this word alone. Empty until the driver has a complete listing.
        """
        return {}

    # -- entities the integration itself creates ----------------------------------------------------
    def registry_entity(self, domain: str, unique_id: str) -> EntityEntry | None:
        """This entry's registry entity of ``domain`` with ``unique_id`` (case-insensitive), disabled ones too."""
        want = str(unique_id).lower()
        return next((e for e in self.hub.entities.values()
                     if e.config_entry_id == self.entry.entry_id and e.domain == domain
                     and str(e.unique_id).lower() == want), None)

    def ensure_device(self, name: str, *, identifiers: list[list[str]] | None = None,
                      connections: list[list[str]] | None = None, **info: Any) -> DeviceEntry:
        """The device this entry's entities belong to, registering it the first time (HA's device_info).

        A device of this entry sharing one of the ``identifiers`` or ``connections`` is reused, else
        the entry's only device, else (as HA's device registry merges them) another integration's
        device with one of those ``connections``, which this entry then joins. ``info`` (manufacturer,
        model...) is confirmed as the device's own.
        """
        pairs = {tuple(map(str, p)) for p in (identifiers or []) + (connections or [])}
        mine = [d for d in self.hub.devices.values() if self.entry.entry_id in d.config_entries]
        dev = next((d for d in mine if pairs & {tuple(map(str, p)) for p in d.identifiers + d.connections}), None)
        if dev is None and len(mine) == 1:
            dev = mine[0]
        conns = {tuple(map(str, p)) for p in connections or []}
        if dev is None and conns:
            dev = next((d for d in self.hub.devices.values()
                        if conns & {tuple(map(str, p)) for p in d.connections}), None)
            if dev is not None:
                dev.config_entries.append(self.entry.entry_id)
                self.hub._save("device", dev)
                self.hub.bus.fire("device_registry_updated", {"action": "update", "device_id": dev.id})
        if dev is None:
            dev = DeviceEntry(id=uuid.uuid4().hex, name=name, config_entries=[self.entry.entry_id])
            self.hub.devices[dev.id] = dev
            self.hub._save("device", dev)
            self.hub.bus.fire("device_registry_updated", {"action": "create", "device_id": dev.id})
            self.log.info("%s: registered device %s", self.entry.title, name)
        fields = {k: v for k, v in info.items() if v not in (None, "")}
        if identifiers:
            fields["identifiers"] = identifiers
        if connections:
            fields["connections"] = connections
        if fields:
            self.hub.confirm_device(dev.id, **fields)
        return dev

    def ensure_entity(self, domain: str, unique_id: str, name: str | None = None, *,
                      device: DeviceEntry | None = None, disabled: bool = False, **info: Any) -> EntityEntry:
        """This entry's entity with ``unique_id``, registering it when the registry has none.

        HA's integrations create their entities when they set up; the hub's registry came from HA's,
        so an entity HA lost (or one a device gained since) would otherwise never appear. A registry
        entity that exists, disabled or not, is returned untouched: a person's choices win. ``name``
        is the entity's own name (``None`` for the device's main entity); ``info`` holds registry info
        fields (``original_device_class``, ``unit_of_measurement``...), confirmed as the device's own.
        ``disabled`` registers it disabled by the integration (diagnostics HA also hides).
        """
        ent = self.registry_entity(domain, unique_id)
        if ent is not None:
            return ent
        from services.homehub.flows import free_entity_id, slugify

        dev_name = (device.display_name if device else "") or self.entry.title
        base = slugify(f"{dev_name} {name}" if name else dev_name)
        ent = EntityEntry(entity_id=free_entity_id(self.hub, domain, base, set()), platform=self.entry.domain,
                          unique_id=unique_id, config_entry_id=self.entry.entry_id,
                          device_id=device.id if device else None, has_entity_name=device is not None,
                          disabled_by="integration" if disabled else None)
        self.hub.entities[ent.entity_id] = ent
        self.hub.inherit_device_hidden(ent)
        self.hub.confirm_entity(ent, original_name=name, **info)
        self.hub._save("entity", ent)
        self.hub.bus.fire("entity_registry_updated", {"action": "create", "entity_id": ent.entity_id})
        self.log.info("%s: registered %s (%s)", self.entry.title, ent.entity_id, unique_id)
        return ent

    @staticmethod
    def user_unit(entity: EntityEntry) -> str | None:
        """The display unit a person chose for a sensor (HA's ``options.sensor.unit_of_measurement``), if any.

        A person's choice may come from the import; the unit the integration reported there may not.
        """
        chosen = (entity.options.get("sensor") or {}).get("unit_of_measurement") if entity.options else None
        return str(chosen) if chosen else None

    def push_fed_entities(self) -> set[str]:
        """Entity ids a live push keeps current even while polls fail; the runtime leaves them available.

        Empty by default: a driver says which (and only while its push is actually connected).
        """
        return set()

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
        #: Drivers for entities with no config entry, by platform: HA's helpers (helpers.py) keep their
        #: registry shape (no config entry), and their entity services reach the manager through this.
        self.platform_drivers: dict[str, Any] = {}
        self.services: dict[tuple[str, str], ServiceHandler] = {}
        self.service_schemas: dict[tuple[str, str], dict[str, Any]] = {}
        #: Services only an admin may call over the API (HA's ``async_register_admin_service``): recorder purges.
        self.admin_services: set[tuple[str, str]] = set()
        self.time_zone = time_zone
        self.location = location or {}
        self.started_at = utcnow()
        #: The Store, when the service runs with one (drivers keeping their own data use it).
        self.store: Any = None
        #: The folder drivers keep files in (camera clips waiting for upload), set by the service.
        self.data_dir: str | None = None
        #: Jarvis's own Nextcloud account (url, username, password), read once at start; None when unset.
        self.household_nextcloud: dict[str, str] | None = None
        #: HA entity-registry uuid -> entity_id; HA device triggers name entities by that uuid.
        self.entity_registry_ids: dict[str, str] = {}
        #: Entities a driver has published a state for since the hub started.
        self.live: set[str] = set()
        #: Combined duplicates (duplicates.py): each copy's entity id -> ``(main, fallback)`` entity ids. Both ids
        #: show one shared state, the main's while it is available, else the fallback's; commands go to the main
        #: and, when it fails, to the fallback (``_call_combined``).
        self.combined: dict[str, tuple[str, str]] = {}
        #: Each combined copy's own last state, as its driver published it.
        self.copy_states: dict[str, State] = {}
        #: Combined mains whose last command the fallback carried out: the fallback's state is the true one until
        #: the main says something new.
        self.fallback_active: set[str] = set()

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

    # -- hidden devices --------------------------------------------------------
    def is_hidden(self, entity_id: str) -> bool:
        """Left out of what people and Jarvis browse: hidden itself, or on a hidden device. It still works."""
        e = self.entities.get(entity_id)
        if e is None:
            return False
        if e.hidden_by:
            return True
        dev = self.devices.get(e.device_id or "")
        return bool(dev and dev.hidden_by)

    def hidden_entity_ids(self) -> list[str]:
        """Every hidden entity, sorted."""
        return sorted(eid for eid in self.entities if self.is_hidden(eid))

    def inherit_device_hidden(self, ent: EntityEntry) -> bool:
        """An entity on a hidden device is hidden by it (``hidden_by: device``, HA's rule for disabling).

        Answers whether it changed; the caller saves. A person's own choice (``user``) is left alone.
        """
        dev = self.devices.get(ent.device_id or "")
        if dev is not None and dev.hidden_by and not ent.hidden_by:
            ent.hidden_by = "device"
            return True
        return False

    def reconcile_hidden(self) -> list[str]:
        """Bring every entity in line with its device's hidden flag (at start, after an import or a resync).

        An entity on a hidden device gains ``hidden_by: device``; one left marked ``device`` by a device that is
        no longer hidden loses it. Answers the entity ids that changed (saved).
        """
        changed = []
        for ent in self.entities.values():
            dev = self.devices.get(ent.device_id or "")
            if self.inherit_device_hidden(ent):
                changed.append(ent.entity_id)
            elif ent.hidden_by == "device" and not (dev is not None and dev.hidden_by):
                ent.hidden_by = None
                changed.append(ent.entity_id)
        for eid in changed:
            self._save("entity", self.entities[eid])
        return changed

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
            if entry.domain == "update" and str(state) != UNAVAILABLE:
                state = update_state(state, attrs, skipped_version(entry))
        if entity_id in self.combined:
            self.copy_states[entity_id] = State(entity_id, "" if state is None else str(state), attrs)
            if self.combined[entity_id][0] == entity_id:
                self.fallback_active.discard(entity_id)
            return self.publish_combined(entity_id)
        return self.states.set(entity_id, state, attrs)

    # -- combined duplicates (duplicates.py) -----------------------------------
    def combined_source(self, entity_id: str) -> tuple[str | None, State | None]:
        """Which copy a combined entity shows: ``("main" | "fallback" | None, that copy's own state)``.

        The main while it is available (unless the fallback carried out the last command and the main has said
        nothing since); else the fallback while that is; else whichever copy has said anything (unavailable).
        """
        main, fallback = self.combined[entity_id]
        ms, fs = self.copy_states.get(main), self.copy_states.get(fallback)
        if main in self.fallback_active and fs is not None and fs.state != UNAVAILABLE:
            return "fallback", fs
        if ms is not None and ms.state != UNAVAILABLE:
            return "main", ms
        if fs is not None and fs.state != UNAVAILABLE:
            return "fallback", fs
        if ms is not None:
            return "main", ms
        return ("fallback", fs) if fs is not None else (None, None)

    def publish_combined(self, entity_id: str) -> State:
        """Write the shared state of a combined entity under both copies' ids (each keeps its own name)."""
        main, fallback = self.combined[entity_id]
        _which, src = self.combined_source(entity_id)
        if src is None:
            current = self.states.get(entity_id)
            return current if current is not None else self.states.set(entity_id, UNKNOWN, {})
        out: State | None = None
        for eid in (main, fallback):
            attrs = dict(src.attributes)
            entry = self.entities.get(eid)
            if entry is not None:
                attrs["friendly_name"] = self.friendly_name(entry)
                if entry.icon:
                    attrs["icon"] = entry.icon
            st = self.states.set(eid, src.state, attrs)
            if eid == entity_id:
                out = st
        assert out is not None
        return out

    def _driver_for(self, entry: EntityEntry) -> Driver | None:
        return self.drivers.get(entry.config_entry_id or "") if entry.config_entry_id \
            else self.platform_drivers.get(entry.platform)

    async def _call_combined(self, main_id: str, fallback_id: str, domain: str, service: str,
                             data: dict[str, Any]) -> Any:
        """Send a command to a combined entity's main copy, and to its fallback when the main fails.

        A main whose own state is unavailable while the fallback's is not is skipped (it would only time out).
        The main gets ``COMBINED_MAIN_TIMEOUT_S`` to answer; an error or a timeout sends the same command to the
        fallback, logged at INFO. Only when both fail does the call fail, with both reasons.
        """
        order: list[tuple[EntityEntry, Driver]] = []
        missing: list[str] = []
        for eid in (main_id, fallback_id):
            ent = self.entities.get(eid)
            drv = self._driver_for(ent) if ent is not None and not ent.disabled_by else None
            if ent is None or drv is None:
                missing.append(f"{eid}: no driver is running for it")
                continue
            order.append((ent, drv))
        if not order:
            raise HubError("; ".join(missing))
        ms, fs = self.copy_states.get(main_id), self.copy_states.get(fallback_id)
        skipped_main = (len(order) == 2 and ms is not None and ms.state == UNAVAILABLE
                        and fs is not None and fs.state != UNAVAILABLE)
        if skipped_main:
            order.reverse()
        errors: list[tuple[str, BaseException]] = []
        main_timeout = COMBINED_PLAY_TIMEOUT_S if (domain, service) in COMBINED_PLAY_SERVICES \
            else COMBINED_MAIN_TIMEOUT_S
        for i, (ent, drv) in enumerate(order):
            try:
                job = drv.async_call(ent, domain, service, dict(data))
                if i == 0 and len(order) > 1:
                    result = await asyncio.wait_for(job, main_timeout)
                else:
                    result = await job
            except Exception as exc:  # any failure of one copy is a reason to try the other
                if isinstance(exc, TimeoutError):
                    exc = DeviceUnavailable(f"did not answer in {main_timeout:g} s")
                errors.append((ent.entity_id, exc))
                continue
            if ent.entity_id != main_id:
                why = "the main copy is unavailable" if skipped_main else \
                    "; ".join(f"{eid}: {exc}" for eid, exc in errors) or "the main copy has no driver"
                log.info("%s.%s on %s used fallback %s (%s)", domain, service, main_id, ent.entity_id, why)
                self.fallback_active.add(main_id)
                if main_id in self.combined:
                    self.publish_combined(main_id)
            return result
        first = errors[0][1]
        exc_type = type(first) if isinstance(first, HubError) else HubError
        raise exc_type("; ".join([*missing, *(f"{eid}: {exc}" for eid, exc in errors)]))

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
        new = (domain, service) not in self.services
        self.services[(domain, service)] = handler
        self.service_schemas[(domain, service)] = schema or {}
        if new:
            # HA's event: automations waiting for a device's own action resume on it.
            self.bus.fire("service_registered", {"domain": domain, "service": service})

    def unregister_service(self, domain: str, service: str) -> None:
        if self.services.pop((domain, service), None) is not None:
            self.bus.fire("service_removed", {"domain": domain, "service": service})
        self.service_schemas.pop((domain, service), None)

    def is_device_action(self, domain: str, service: str) -> bool:
        """Whether ``domain.service`` names an action a device brings with it (it exists only while the device
        is connected or set up), as opposed to a service the hub or an integration always has. Which names are
        device actions is declared by the drivers that register them (``drivers.DEVICE_ACTIONS``)."""
        from services.homehub.drivers import DEVICE_ACTIONS

        return any(service.startswith(prefix) and len(service) > len(prefix)
                   for prefix in DEVICE_ACTIONS.get(domain, ()))

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
                # A hidden device's entities are left out of a room (it is the duplicate of one that stays);
                # named by entity or device id they still run (HA skips hidden entities in areas too).
                ids.extend(eid for eid in self.area_entities(area)
                           if (e := self.entities.get(eid)) is None or e.hidden_by != "device")
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
            # entity_ids: the entities the call resolved to (areas, devices and labels
            # included), so a listener need not resolve targets itself.
            self.bus.fire("call_service", {"domain": domain, "service": service, "service_data": data,
                                           "entity_ids": list(targets)})
            handler = self.services.get((domain, service))
            if handler is not None:
                response = await handler(self, call)
            elif domain == "update" and service in UPDATE_SKIP_SERVICES:
                self._update_skip(call)
            elif domain == "update" and service == "install":
                self._check_installable(call)
                await self._dispatch_entities(call)
            elif domain == "homeassistant" and service in ("turn_on", "turn_off", "toggle"):
                await self._dispatch_entities(call, by_entity_domain=True)
            else:
                await self._dispatch_entities(call)
        finally:
            unsub()
        return {"changed_states": changed, "response": response}

    # -- update entities: the skipped version is the hub's own record ----------
    def _update_targets(self, call: ServiceCall) -> list[EntityEntry]:
        if not call.entity_ids:
            raise ServiceNotFound(f"update.{call.service} needs a target entity")
        out = []
        for eid in call.entity_ids:
            ent = self.entities.get(eid)
            if ent is None or ent.disabled_by:
                raise EntityNotFound(f"{eid}: unknown entity")
            if ent.domain == "update":
                out.append(ent)
        if not out:
            raise ServiceNotFound(f"update.{call.service} needs an update entity")
        return out

    def _update_skip(self, call: ServiceCall) -> None:
        """``update.skip`` / ``update.clear_skipped`` with HA's rules, kept in the entity's options.

        Every target is checked before any is changed, so a refused call changes nothing.
        """
        targets = self._update_targets(call)
        skips: dict[str, str | None] = {}
        for ent in targets:
            st = self.states.get(ent.entity_id)
            if st is None or st.state == UNAVAILABLE:
                raise DeviceUnavailable(f"{ent.entity_id} is unavailable")
            if call.service == "skip":
                latest = st.attributes.get("latest_version")
                if not latest or latest == st.attributes.get("installed_version"):
                    raise HubError(f"{ent.entity_id} has no update to skip")
                skips[ent.entity_id] = str(latest)
            else:
                skips[ent.entity_id] = None
        for ent in targets:
            opts = dict(ent.options or {})
            upd = {k: v for k, v in dict(opts.get("update") or {}).items() if k != "skipped_version"}
            if skips[ent.entity_id]:
                upd["skipped_version"] = skips[ent.entity_id]
            if upd:
                opts["update"] = upd
            else:
                opts.pop("update", None)
            ent.options = opts
            self._save("entity", ent)
            st = self.states.get(ent.entity_id)
            if st is not None:
                self.publish(ent, st.state, dict(st.attributes))

    def _check_installable(self, call: ServiceCall) -> None:
        """Refuse ``update.install`` on an entity whose integration cannot install (it does not advertise it)."""
        for ent in self._update_targets(call):
            st = self.states.get(ent.entity_id)
            try:
                features = int((st.attributes.get("supported_features") if st else None) or 0)
            except (TypeError, ValueError):
                features = 0
            if not features & UPDATE_INSTALL:
                raise HubError(f"{ent.entity_id} cannot be installed from the hub: its integration has no way "
                               "to install firmware")

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
        combined_done: set[str] = set()
        for eid in call.entity_ids:
            pair = self.combined.get(eid)
            if pair is not None:
                # Either copy's id is the combined device: one command, to the main (then the fallback).
                main_entry = self.entities.get(pair[0])
                if pair[0] in combined_done or main_entry is None:
                    continue
                domain = main_entry.domain if by_entity_domain else call.domain
                if not by_entity_domain and main_entry.domain != call.domain and call.domain != "homeassistant":
                    continue
                combined_done.add(pair[0])
                jobs.append((pair[0], self._call_combined(pair[0], pair[1], domain, call.service, dict(data))))
                continue
            entry = self.entities.get(eid)
            if entry is None or entry.disabled_by:
                errors.append(f"{eid}: unknown entity")
                continue
            domain = entry.domain if by_entity_domain else call.domain
            if not by_entity_domain and entry.domain != call.domain and call.domain != "homeassistant":
                # HA silently skips entities of another domain; so do we.
                continue
            driver = self._driver_for(entry)
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


def skipped_version(entity: EntityEntry) -> str | None:
    """The version a person chose to skip for an update entity (``options.update.skipped_version``)."""
    value = ((entity.options or {}).get("update") or {}).get("skipped_version")
    return str(value) if value else None


def update_state(state: Any, attrs: dict[str, Any], skipped: str | None) -> Any:
    """HA's update state: ``on`` while a version newer than the installed one is offered and not skipped.

    ``attrs`` gets the ``skipped_version`` the hub holds. Without both versions, or while the driver says it
    does not know yet (``unknown``: it has not asked for the latest version), the driver's state stands.
    """
    attrs["skipped_version"] = skipped
    latest, installed = attrs.get("latest_version"), attrs.get("installed_version")
    if not latest or not installed or str(state) == UNKNOWN:
        return state
    return "on" if str(latest) != str(installed) and str(latest) != skipped else "off"


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
    "update": ("install", "skip", "clear_skipped"),
    "todo": ("add_item", "update_item", "remove_item", "remove_completed_items", "get_items"),
    "camera": ("snapshot",),
    "assist_satellite": ("announce",),
}
