"""ESPHome nodes over the native API (aioesphomeapi), one connection per node.

Entry data, as HA stored it: ``host``, ``port``, ``password``, ``noise_psk``,
``device_name``. The node pushes every state change over the connection, so
there is no polling: :meth:`async_poll` is only a watchdog that restarts the
reconnect loop if it ever stopped. aioesphomeapi pings the node every 20 s and
drops the connection when a ping goes unanswered; ``on_stop`` then marks the
node's entities unavailable and reconnects with backoff.

Registry entities map to the node's entities by unique_id. HA's current format
is ``{MAC}/{device_id}/{platform}/{entity name}`` (``02:00:00:00:3E:F8/0/light/LCD Backlight``;
the name may contain ``/`` and is empty for the node's main entity, as in
``.../light/``). Text sensors keep ``text_sensor`` there although the HA domain
is ``sensor``. HA's older ``{node}{platform}{object_id}`` ids are matched too.
Ids like ``{MAC}-pipeline``, ``-wake_word``, ``-vad_sensitivity`` and
``-assist_satellite`` are not on the node at all: HA made them for its voice
pipeline. They keep their last state and a ``select_option`` on them is
remembered, nothing more.

User-defined actions on the node (``api: actions:``) are registered as hub
services ``esphome.{node_name}_{action}`` (dashes in the node name become
underscores, as HA does), so ``esphome.kitchen_node_set_mode`` keeps
working. With ``allow_service_calls`` in the entry options, actions the node asks
for (``homeassistant.action``) run on the hub, and states the node subscribes to
(``homeassistant`` sensors) are sent to it as they change, which is what the
voice satellites and screens rely on.

Everything about what a node and its entities are comes from the node on every
connect: ``device_info`` gives the maker, project/model, firmware, MAC and web
UI (``device_registry_fields``, shared with the flow that adds a node), and
each entity info its name, device class, unit, state class, category, icon,
feature flags, colour modes, ranges and options. They are confirmed onto the
registry (``confirm_device`` / ``confirm_entity``) and published with every
state. The ``-pipeline``/``-wake_word`` entities HA made for its voice pipeline
are not on the node, so nothing about them is confirmed.
"""
from __future__ import annotations

import asyncio
import colorsys
import contextlib
import json
import math
import re
from datetime import UTC, datetime
from typing import Any

from aioesphomeapi import (
    APIClient,
    APIConnectionError,
    APIVersion,
    BinarySensorInfo,
    ButtonInfo,
    CameraInfo,
    ColorMode,
    CoverInfo,
    CoverOperation,
    FanDirection,
    FanInfo,
    LightColorCapability,
    LightInfo,
    LockCommand,
    LockInfo,
    LockState,
    MediaPlayerCommand,
    MediaPlayerInfo,
    MediaPlayerState,
    NumberInfo,
    NumberMode,
    SelectInfo,
    SensorInfo,
    SensorStateClass,
    SirenInfo,
    SwitchInfo,
    TextInfo,
    TextSensorInfo,
    UserService,
    UserServiceArgType,
    ValveInfo,
    ValveOperation,
    VoiceAssistantFeature,
)

from services.homehub.core import (
    UNAVAILABLE,
    UNKNOWN,
    DeviceUnavailable,
    Driver,
    EntityEntry,
    HubError,
    ServiceCall,
    ServiceNotFound,
    utcnow,
)
from services.homehub.drivers.mjpeg import CameraTokens, camera_attributes

DEFAULT_PORT = 6053
CONNECT_TIMEOUT = 5.0
LIST_TIMEOUT = 5.0
COMMAND_SETTLE = 1.0  # how long a command waits for the node to push the new state
SERVICE_TIMEOUT = 5.0
#: A camera frame (ESP32 cameras send one in well under a second when idle).
CAMERA_TIMEOUT_S = 10.0
SATELLITE_ANNOUNCE_TIMEOUT_S = 120.0  # a long announcement plays to the end before the node answers
RECONNECT_DELAYS = (2, 5, 10, 30, 60)

# EntityInfo class -> the platform word HA put in the unique_id.
PLATFORMS: dict[str, str] = {
    "BinarySensorInfo": "binary_sensor", "ButtonInfo": "button", "CameraInfo": "camera",
    "ClimateInfo": "climate", "CoverInfo": "cover", "FanInfo": "fan", "LightInfo": "light",
    "LockInfo": "lock", "MediaPlayerInfo": "media_player", "NumberInfo": "number",
    "SelectInfo": "select", "SensorInfo": "sensor", "SirenInfo": "siren", "SwitchInfo": "switch",
    "TextSensorInfo": "text_sensor", "TextInfo": "text", "ValveInfo": "valve", "EventInfo": "event",
    "DateInfo": "date", "TimeInfo": "time", "DateTimeInfo": "datetime", "UpdateInfo": "update",
    "AlarmControlPanelInfo": "alarm_control_panel", "WaterHeaterInfo": "water_heater",
}

_NEW_UID = re.compile(r"^([0-9A-Fa-f:]{17})/(\d+)/([a-z_]+)/(.*)$", re.S)

STATE_CLASSES = {SensorStateClass.MEASUREMENT: "measurement", SensorStateClass.TOTAL_INCREASING: "total_increasing",
                 SensorStateClass.TOTAL: "total"}
NUMBER_MODES = {NumberMode.AUTO: "auto", NumberMode.BOX: "box", NumberMode.SLIDER: "slider"}
LOCK_STATES = {LockState.LOCKED: "locked", LockState.UNLOCKED: "unlocked", LockState.JAMMED: "jammed",
               LockState.LOCKING: "locking", LockState.UNLOCKING: "unlocking", LockState.OPENING: "opening",
               LockState.OPEN: "open"}
MEDIA_STATES = {MediaPlayerState.IDLE: "idle", MediaPlayerState.PLAYING: "playing",
                MediaPlayerState.PAUSED: "paused", MediaPlayerState.ANNOUNCING: "playing",
                MediaPlayerState.OFF: "off", MediaPlayerState.ON: "on", MediaPlayerState.NONE: "idle"}

# HA feature bits
LIGHT_EFFECT, LIGHT_FLASH, LIGHT_TRANSITION = 4, 8, 32
COVER_OPEN, COVER_CLOSE, COVER_SET_POSITION, COVER_STOP = 1, 2, 4, 8
COVER_OPEN_TILT, COVER_CLOSE_TILT, COVER_STOP_TILT, COVER_SET_TILT = 16, 32, 64, 128
VALVE_OPEN, VALVE_CLOSE, VALVE_SET_POSITION, VALVE_STOP = 1, 2, 4, 8
FAN_SET_SPEED, FAN_OSCILLATE, FAN_DIRECTION, FAN_PRESET, FAN_TURN_OFF, FAN_TURN_ON = 1, 2, 4, 8, 16, 32
LOCK_OPEN = 1
SIREN_TURN_ON, SIREN_TURN_OFF, SIREN_TONES, SIREN_VOLUME, SIREN_DURATION = 1, 2, 4, 8, 16


def device_registry_fields(device_info: Any) -> dict[str, Any]:
    """The DeviceEntry fields a node's ``device_info`` gives (the flow and the driver both use this)."""
    from services.homehub.discovery import norm_mac

    mac = norm_mac(str(getattr(device_info, "mac_address", "") or ""))
    version = getattr(device_info, "esphome_version", "") or ""
    project_version = getattr(device_info, "project_version", "") or ""
    name = getattr(device_info, "name", "") or ""
    return {
        "manufacturer": getattr(device_info, "manufacturer", "") or "Espressif",
        "model": getattr(device_info, "project_name", "") or getattr(device_info, "model", "") or None,
        "sw_version": f"{project_version} (ESPHome {version})" if project_version
        else (f"ESPHome {version}" if version else None),
        "connections": [["mac", ":".join(mac[i:i + 2] for i in range(0, 12, 2))]] if mac else [],
        "configuration_url": f"http://{name}.local/" if name and getattr(device_info, "webserver_port", 0) else None,
    }


def entity_registry_fields(info: Any) -> dict[str, Any]:
    """The EntityEntry fields an entity info gives (category, class, unit, icon, its own name)."""
    out: dict[str, Any] = {
        "entity_category": {1: "config", 2: "diagnostic"}.get(int(getattr(info, "entity_category", 0) or 0)),
        "original_device_class": getattr(info, "device_class", "") or None,
        "unit_of_measurement": getattr(info, "unit_of_measurement", "") or None,
        "original_icon": getattr(info, "icon", "") or None,
        "original_name": getattr(info, "name", "") or None,
    }
    return {k: v for k, v in out.items() if v is not None}


def platform_of(info: Any) -> str:
    return PLATFORMS.get(type(info).__name__, type(info).__name__.removesuffix("Info").lower())


def ha_color_mode(mode: int) -> str | None:
    """ESPHome's ColorMode is a bitmask of capabilities; HA names the combination."""
    caps = int(mode)
    if caps == 0:
        return None
    rgb = caps & LightColorCapability.RGB
    white = caps & LightColorCapability.WHITE
    ct = caps & (LightColorCapability.COLOR_TEMPERATURE | LightColorCapability.COLD_WARM_WHITE)
    if rgb and ct:
        return "rgbww"
    if rgb and white:
        return "rgbw"
    if rgb:
        return "rgb"
    if ct:
        return "color_temp"
    if white:
        return "white"
    if caps & LightColorCapability.BRIGHTNESS:
        return "brightness"
    return "onoff"


def _convert_arg(kind: Any, value: Any) -> Any:
    def one(k: Any, v: Any) -> Any:
        if k == UserServiceArgType.BOOL:
            return v.strip().lower() in ("1", "true", "on", "yes") if isinstance(v, str) else bool(v)
        if k == UserServiceArgType.INT:
            return int(float(v))
        if k == UserServiceArgType.FLOAT:
            return float(v)
        return "" if v is None else str(v)

    single = {UserServiceArgType.BOOL_ARRAY: UserServiceArgType.BOOL,
              UserServiceArgType.INT_ARRAY: UserServiceArgType.INT,
              UserServiceArgType.FLOAT_ARRAY: UserServiceArgType.FLOAT,
              UserServiceArgType.STRING_ARRAY: UserServiceArgType.STRING}
    if kind in single:
        items = value if isinstance(value, list | tuple) else [value]
        return [one(single[kind], x) for x in items]
    return one(kind, value)


class ESPHomeDriver(Driver):
    platform = "esphome"
    # Push-only in practice; the poll is a watchdog for the reconnect loop.
    poll_interval = 60.0

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        d = entry.data
        # An address in the options is the owner's override and is the only one
        # tried. Otherwise the stored host comes first and the node's mDNS name
        # (``{device_name}.local``) is the fallback: HA kept the stored IP current
        # through mDNS, so after HA it can go stale when DHCP moves a node.
        pinned = entry.options.get("host")
        self.host: str = str(pinned or d.get("host") or "")
        self.candidates: list[str] = [self.host] if self.host else []
        self.device_name: str = str(d.get("device_name") or "")
        if not pinned and self.device_name:
            mdns = f"{self.device_name}.local"
            if mdns.lower() != self.host.lower():
                self.candidates.append(mdns)
        self.port: int = int(d.get("port") or DEFAULT_PORT)
        self.password: str = str(d.get("password") or "")
        self.noise_psk: str | None = d.get("noise_psk") or None
        self.allow_service_calls: bool = bool(entry.options.get("allow_service_calls"))
        self._client: Any = None
        self._device_info: Any = None
        self._api_version: Any = None
        self._closing = False
        self._reconnect_task: asyncio.Task | None = None
        self._was_connected = False
        self._infos: dict[tuple[int, int], Any] = {}         # (device_id, key) -> EntityInfo
        self._entity_for: dict[tuple[int, int], EntityEntry] = {}
        self._info_for: dict[str, Any] = {}                    # entity_id -> EntityInfo
        self._last: dict[tuple[int, int], Any] = {}            # last EntityState per key
        self._waiters: dict[tuple[int, int], list[asyncio.Future]] = {}
        self._camera_tokens = CameraTokens()
        self._services: list[tuple[str, str]] = []
        self._ha_subs: dict[str, set[str | None]] = {}        # HA entity_id -> attributes the node wants
        self._unsub_bus = None
        self._tasks: set[asyncio.Task] = set()
        self.local_entities = [e for e in self.entities if "/" not in (e.unique_id or "")
                               and re.match(r"^[0-9A-Fa-f:]{17}-", e.unique_id or "")]

    # -- connection -----------------------------------------------------------
    def _make_client(self, host: str) -> Any:
        # aioesphomeapi resolves a ``.local`` name itself through zeroconf (host_resolver).
        return APIClient(host, self.port, self.password, client_info="homehub",
                         noise_psk=self.noise_psk, expected_name=self.device_name or None)

    @property
    def connected(self) -> bool:
        return self._client is not None

    async def _try_connect(self, host: str) -> tuple[Any, Any, list[Any], list[Any]]:
        client = self._make_client(host)
        try:
            await asyncio.wait_for(client.connect(on_stop=self._on_stop, login=True), CONNECT_TIMEOUT)
            device_info, infos, services = await asyncio.wait_for(
                client.device_info_and_list_entities(), LIST_TIMEOUT)
        except (APIConnectionError, OSError, TimeoutError) as exc:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.disconnect(force=True), 2)
            raise DeviceUnavailable(f"{host}:{self.port}: {exc or type(exc).__name__}") from exc
        return client, device_info, infos, services

    async def _connect(self) -> None:
        # The address that worked last goes first, so a fallback that answered
        # keeps being used and the stale one is only retried after it.
        order = [self.host] + [h for h in self.candidates if h != self.host]
        errors = []
        for host in order:
            try:
                client, device_info, infos, services = await self._try_connect(host)
            except DeviceUnavailable as exc:
                errors.append(str(exc))
                continue
            if host != self.host:
                self.log.warning("ESPHome %s answered at %s, not %s; using that address from now on",
                                 self.device_name, host, self.host)
                self.host = host
            break
        else:
            raise DeviceUnavailable(
                f"ESPHome {self.device_name or self.host} unreachable: {'; '.join(errors)}")
        self._client = client
        self._api_version = getattr(client, "api_version", None) or APIVersion(1, 10)
        self._index(infos)
        self._device_info = device_info
        self._confirm(device_info)
        self._register_services(getattr(device_info, "name", "") or self.device_name, services)
        client.subscribe_home_assistant_states_and_services(
            on_state=self._on_state, on_service_call=self._on_service_call,
            on_state_sub=self._on_state_sub, on_state_request=self._on_state_request)
        if self._was_connected:
            self.log.info("ESPHome %s (%s) reconnected", self.device_name, self.host)
        self._was_connected = True

    async def _on_stop(self, expected_disconnect: bool) -> None:
        self._client = None
        if self._closing:
            return
        self.log.warning("ESPHome %s (%s) disconnected%s; reconnecting", self.device_name, self.host,
                         "" if expected_disconnect else " unexpectedly")
        self.mark_unavailable(self._device_entities())
        self._start_reconnect()

    def _start_reconnect(self) -> None:
        if self._closing or (self._reconnect_task is not None and not self._reconnect_task.done()):
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop(), name=f"esphome:{self.device_name}")

    async def _reconnect_loop(self) -> None:
        attempt = 0
        while not self._closing and self._client is None:
            delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            await asyncio.sleep(delay)
            if self._closing or self._client is not None:
                return
            try:
                await self._connect()
                return
            except DeviceUnavailable as exc:
                (self.log.warning if attempt == 0 else self.log.debug)("%s; retry in %ss", exc, delay)
            except Exception:
                self.log.exception("ESPHome %s reconnect failed", self.device_name)
            attempt += 1

    async def async_setup(self) -> None:
        if not self.candidates:
            raise DeviceUnavailable("ESPHome entry has no host and no device_name")
        self.host = self.host or self.candidates[0]
        self._unsub_bus = self.hub.bus.subscribe(self._on_hub_state, "state_changed")
        try:
            await self._connect()
        except Exception:
            if self._unsub_bus:
                self._unsub_bus()
                self._unsub_bus = None
            raise

    async def async_poll(self) -> None:
        if self._client is None:
            self._start_reconnect()

    async def async_unload(self) -> None:
        self._closing = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reconnect_task
        for t in list(self._tasks):
            t.cancel()
        self._unregister_services()
        if self._unsub_bus:
            self._unsub_bus()
            self._unsub_bus = None
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.disconnect(), 3)

    def entity_abilities(self, entity: EntityEntry) -> list[str]:
        """What a voice satellite can do, from the node's own voice-assistant feature flags.

        ``announce`` when the node says it takes announcements. Never ``converse``: starting a
        conversation needs an Assist pipeline to hear the answer, which the hub does not run.
        """
        if entity.domain != "assist_satellite" or self._device_info is None:
            return []
        flags = int(getattr(self._device_info, "voice_assistant_feature_flags", 0) or 0)
        return ["announce"] if flags & int(VoiceAssistantFeature.ANNOUNCE) else []

    def _confirm(self, device_info: Any) -> None:
        """What the node said about itself and its entities, onto the registry as the device's own."""
        fields = device_registry_fields(device_info)
        for dev_id in {e.device_id for e in self.entities if e.device_id}:
            self.confirm_device(dev_id, **fields)
        for entity_id, info in self._info_for.items():
            self.confirm_entity(entity_id, **entity_registry_fields(info))

    def _device_entities(self) -> list[EntityEntry]:
        local = {e.entity_id for e in self.local_entities}
        return [e for e in self.entities if e.entity_id not in local]

    # -- entity mapping -------------------------------------------------------
    def _index(self, infos: list[Any]) -> None:
        self._infos = {(getattr(i, "device_id", 0) or 0, i.key): i for i in infos}
        exact: dict[tuple[int, str, str], Any] = {}
        by_name: dict[tuple[str, str], Any] = {}
        by_object: dict[tuple[str, str], Any] = {}
        for i in infos:
            plat = platform_of(i)
            dev = getattr(i, "device_id", 0) or 0
            exact[(dev, plat, i.name)] = i
            by_name.setdefault((plat, i.name.lower()), i)
            if i.object_id:
                by_object.setdefault((plat, i.object_id), i)
        self._entity_for.clear()
        self._info_for.clear()
        unmatched = []
        for e in self._device_entities():
            uid = e.unique_id or ""
            info = None
            if m := _NEW_UID.match(uid):
                dev, plat, name = int(m.group(2)), m.group(3), m.group(4)
                info = exact.get((dev, plat, name)) or by_name.get((plat, name.lower()))
            else:
                # Older HA: "{node name}{platform}{object_id}"
                for (plat, obj), cand in by_object.items():
                    if uid.endswith(f"{plat}{obj}"):
                        info = cand
                        break
            if info is None:
                unmatched.append(e)
                continue
            self._entity_for[(getattr(info, "device_id", 0) or 0, info.key)] = e
            self._info_for[e.entity_id] = info
        if unmatched:
            self.log.info("ESPHome %s: %d registry entities are not on the node: %s", self.device_name,
                          len(unmatched), ", ".join(e.entity_id for e in unmatched))
            self.mark_unavailable(unmatched)
        # Entities with no state message (buttons, cameras) still get a state now.
        for k, e in self._entity_for.items():
            info = self._infos[k]
            if isinstance(info, ButtonInfo):
                cur = self.hub.states.get(e.entity_id)
                self.publish(e, cur.state if cur and cur.state != UNAVAILABLE else UNKNOWN, self._attrs(info))
            elif isinstance(info, CameraInfo):
                self.publish(e, "idle", camera_attributes(e.entity_id, self._camera_tokens.current(e.entity_id)))

    # -- states ---------------------------------------------------------------
    def _on_state(self, state: Any) -> None:
        k = (getattr(state, "device_id", 0) or 0, state.key)
        self._last[k] = state
        for fut in self._waiters.pop(k, []):
            if not fut.done():
                fut.set_result(state)
        entity, info = self._entity_for.get(k), self._infos.get(k)
        if entity is None or info is None:
            return
        try:
            value, attrs = self._convert(info, state)
        except Exception:
            self.log.exception("ESPHome %s: could not read the state of %s", self.device_name, entity.entity_id)
            return
        self.publish(entity, value, {**self._attrs(info), **attrs})

    def _attrs(self, info: Any) -> dict[str, Any]:
        """Static attributes from the entity info."""
        a: dict[str, Any] = {}
        if getattr(info, "device_class", ""):
            a["device_class"] = info.device_class
        if getattr(info, "icon", ""):
            a["icon"] = info.icon
        if isinstance(info, SensorInfo):
            if info.unit_of_measurement:
                a["unit_of_measurement"] = info.unit_of_measurement
            if info.state_class in STATE_CLASSES:
                a["state_class"] = STATE_CLASSES[info.state_class]
        elif isinstance(info, NumberInfo):
            a.update({"min": info.min_value, "max": info.max_value, "step": info.step,
                      "mode": NUMBER_MODES.get(info.mode, "auto")})
            if info.unit_of_measurement:
                a["unit_of_measurement"] = info.unit_of_measurement
        elif isinstance(info, SelectInfo):
            a["options"] = list(info.options)
        elif isinstance(info, LightInfo):
            modes = self._light_modes(info)
            ha_modes = []
            for m in modes:
                hm = ha_color_mode(m)
                if hm and hm not in ha_modes:
                    ha_modes.append(hm)
            # HA never lists onoff/brightness beside a richer mode.
            if len(ha_modes) > 1:
                ha_modes = [m for m in ha_modes if m not in ("onoff", "brightness")] or ha_modes
            a["supported_color_modes"] = ha_modes or ["onoff"]
            feats = 0
            if any(int(m) & LightColorCapability.BRIGHTNESS for m in modes):
                feats |= LIGHT_FLASH | LIGHT_TRANSITION
            if [e for e in info.effects if e != "None"]:
                feats |= LIGHT_EFFECT
                a["effect_list"] = list(info.effects)
            a["supported_features"] = feats
            if info.min_mireds > 0 and info.max_mireds > 0:
                a["min_color_temp_kelvin"] = round(1_000_000 / info.max_mireds)
                a["max_color_temp_kelvin"] = round(1_000_000 / info.min_mireds)
        elif isinstance(info, CoverInfo):
            f = COVER_OPEN | COVER_CLOSE
            if info.supports_stop:
                f |= COVER_STOP
            if info.supports_position:
                f |= COVER_SET_POSITION
            if info.supports_tilt:
                f |= COVER_OPEN_TILT | COVER_CLOSE_TILT | COVER_SET_TILT
                if getattr(info, "supports_stop_tilt", False) or info.supports_stop:
                    f |= COVER_STOP_TILT
            a["supported_features"] = f
        elif isinstance(info, ValveInfo):
            f = VALVE_OPEN | VALVE_CLOSE
            if info.supports_stop:
                f |= VALVE_STOP
            if info.supports_position:
                f |= VALVE_SET_POSITION
            a["supported_features"] = f
        elif isinstance(info, FanInfo):
            f = FAN_TURN_ON | FAN_TURN_OFF
            if info.supports_speed:
                f |= FAN_SET_SPEED
            if info.supports_oscillation:
                f |= FAN_OSCILLATE
            if info.supports_direction:
                f |= FAN_DIRECTION
            if info.supported_preset_modes:
                f |= FAN_PRESET
                a["preset_modes"] = list(info.supported_preset_modes)
            a["supported_features"] = f
        elif isinstance(info, LockInfo):
            a["supported_features"] = LOCK_OPEN if info.supports_open else 0
        elif isinstance(info, SirenInfo):
            f = SIREN_TURN_ON | SIREN_TURN_OFF
            if info.tones:
                f |= SIREN_TONES
                a["available_tones"] = list(info.tones)
            if info.supports_volume:
                f |= SIREN_VOLUME
            if info.supports_duration:
                f |= SIREN_DURATION
            a["supported_features"] = f
        elif isinstance(info, MediaPlayerInfo):
            a["supported_features"] = int(info.feature_flags_compat(self._api_version or APIVersion(1, 10)))
        return a

    def _light_modes(self, info: LightInfo) -> list[Any]:
        try:
            return list(info.supported_color_modes_compat(self._api_version or APIVersion(1, 10)))
        except Exception:
            return list(info.supported_color_modes)

    def _convert(self, info: Any, st: Any) -> tuple[Any, dict[str, Any]]:
        if getattr(st, "missing_state", False):
            return UNKNOWN, {}
        if isinstance(info, SensorInfo):
            v = st.state
            if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                return UNKNOWN, {}
            if info.device_class == "timestamp":
                return datetime.fromtimestamp(v, UTC).isoformat(), {}
            decimals = max(0, int(info.accuracy_decimals or 0))
            return f"{v:.{decimals}f}", {}
        if isinstance(info, TextSensorInfo | TextInfo):
            return st.state, {}
        if isinstance(info, BinarySensorInfo | SwitchInfo):
            return ("on" if st.state else "off"), {}
        if isinstance(info, NumberInfo):
            v = st.state
            return (UNKNOWN if v is None or (isinstance(v, float) and math.isnan(v)) else v), {}
        if isinstance(info, SelectInfo):
            return (st.state or UNKNOWN), {}
        if isinstance(info, LightInfo):
            return self._light_state(st)
        if isinstance(info, CoverInfo):
            op = getattr(st, "current_operation", None)
            pos = getattr(st, "position", 0.0)
            attrs: dict[str, Any] = {}
            if info.supports_position:
                attrs["current_position"] = round(pos * 100)
            if info.supports_tilt:
                attrs["current_tilt_position"] = round(getattr(st, "tilt", 0.0) * 100)
            if op == CoverOperation.IS_OPENING:
                return "opening", attrs
            if op == CoverOperation.IS_CLOSING:
                return "closing", attrs
            return ("closed" if pos <= 0.0 else "open"), attrs
        if isinstance(info, ValveInfo):
            op = getattr(st, "current_operation", None)
            pos = getattr(st, "position", 0.0)
            attrs = {"current_position": round(pos * 100)} if info.supports_position else {}
            if op == ValveOperation.IS_OPENING:
                return "opening", attrs
            if op == ValveOperation.IS_CLOSING:
                return "closing", attrs
            return ("closed" if pos <= 0.0 else "open"), attrs
        if isinstance(info, FanInfo):
            attrs = {}
            if info.supports_speed and info.supported_speed_count:
                attrs["percentage"] = round(st.speed_level / info.supported_speed_count * 100) if st.state else 0
                attrs["percentage_step"] = 100 / info.supported_speed_count
            if info.supports_oscillation:
                attrs["oscillating"] = st.oscillating
            if info.supports_direction:
                attrs["direction"] = "reverse" if int(getattr(st, "direction", 0) or 0) else "forward"
            if info.supported_preset_modes:
                attrs["preset_mode"] = st.preset_mode or None
            return ("on" if st.state else "off"), attrs
        if isinstance(info, LockInfo):
            return LOCK_STATES.get(st.state, UNKNOWN), {}
        if isinstance(info, SirenInfo):
            return ("on" if st.state else "off"), {}
        if isinstance(info, MediaPlayerInfo):
            return MEDIA_STATES.get(st.state, "idle"), {"volume_level": round(st.volume, 3),
                                                          "is_volume_muted": bool(st.muted)}
        if isinstance(info, CameraInfo):
            # A frame arrives as a state; it changes nothing a reader sees.
            ent = self._entity_for.get((getattr(st, "device_id", 0) or 0, st.key))
            token = self._camera_tokens.current(ent.entity_id) if ent is not None else ""
            return "idle", camera_attributes(ent.entity_id, token) if ent is not None else {}
        return getattr(st, "state", UNKNOWN), {}

    def _light_state(self, st: Any) -> tuple[str, dict[str, Any]]:
        attrs: dict[str, Any] = {"color_mode": None, "brightness": None, "color_temp_kelvin": None,
                                 "rgb_color": None, "hs_color": None, "effect": None}
        if not st.state:
            return "off", attrs
        mode = int(st.color_mode)
        caps = mode
        attrs["color_mode"] = ha_color_mode(mode) or "onoff"
        if caps & LightColorCapability.BRIGHTNESS or caps == 0:
            attrs["brightness"] = round(st.brightness * 255)
        if caps & LightColorCapability.RGB:
            rgb = [round(st.red * 255), round(st.green * 255), round(st.blue * 255)]
            attrs["rgb_color"] = rgb
            h, s, _ = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
            attrs["hs_color"] = [round(h * 360, 3), round(s * 100, 3)]
        if caps & (LightColorCapability.COLOR_TEMPERATURE | LightColorCapability.COLD_WARM_WHITE) \
                and st.color_temperature > 0:
            attrs["color_temp_kelvin"] = round(1_000_000 / st.color_temperature)
        if st.effect:
            attrs["effect"] = st.effect
        if mode == ColorMode.UNKNOWN:
            attrs["color_mode"] = "onoff" if not st.brightness else "brightness"
        return "on", attrs

    # -- services -------------------------------------------------------------
    def _require_client(self) -> Any:
        if self._client is None:
            raise DeviceUnavailable(f"ESPHome {self.device_name or self.host} is not connected")
        return self._client

    # -- the camera contract (see drivers/mjpeg.py) ---------------------------
    def access_tokens(self, entity_id: str) -> list[str]:
        return self._camera_tokens.valid(entity_id)

    async def async_camera_image(self, entity: EntityEntry) -> bytes:
        """One frame from an ESPHome camera: ask for a single image, wait for it to arrive as a state."""
        key = next((k for k, e in self._entity_for.items() if e.entity_id == entity.entity_id), None)
        if key is None or not isinstance(self._infos.get(key), CameraInfo):
            raise ServiceNotFound(f"{entity.entity_id} is not a camera on ESPHome node {self.device_name}")
        if self._client is None:
            raise DeviceUnavailable(f"ESPHome {self.device_name or self.host} is not connected")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(key, []).append(fut)
        try:
            self._client.request_single_image()
            state = await asyncio.wait_for(fut, CAMERA_TIMEOUT_S)
        except TimeoutError as exc:
            raise DeviceUnavailable(f"ESPHome camera {entity.entity_id} sent no image") from exc
        finally:
            waiting = self._waiters.get(key, [])
            if fut in waiting:
                waiting.remove(fut)
        return bytes(state.data)

    async def async_camera_stream_url(self, entity: EntityEntry) -> str | None:
        return None  # no stream URL; the API serves frames from async_camera_image

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if entity.domain == "assist_satellite" and domain == "assist_satellite":
            return await self._satellite_call(service, data)
        if entity in self.local_entities or any(e.entity_id == entity.entity_id for e in self.local_entities):
            return self._local_call(entity, domain, service, data)
        info = self._info_for.get(entity.entity_id)
        if info is None:
            if self._client is None:
                raise DeviceUnavailable(f"ESPHome {self.device_name or self.host} is not connected")
            raise ServiceNotFound(f"{entity.entity_id} is not on ESPHome node {self.device_name}")
        client = self._require_client()
        k = (getattr(info, "device_id", 0) or 0, info.key)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(k, []).append(fut)
        try:
            self._send(client, info, service, data, k)
        except APIConnectionError as exc:
            raise DeviceUnavailable(f"ESPHome {self.device_name}: {exc}") from exc
        except BaseException:
            fut.cancel()
            raise
        if isinstance(info, ButtonInfo):
            fut.cancel()
            self.publish(entity, utcnow().isoformat(), self._attrs(info))
            return None
        # The node pushes the new state; give it a moment so the caller's
        # changed_states (and an immediate read) already show it.
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(fut, COMMAND_SETTLE)
        return None

    async def _satellite_call(self, service: str, data: dict[str, Any]) -> Any:
        """HA's ``assist_satellite.announce`` on a voice satellite: the node's own announcement call.

        The text is spoken by Jarvis's voice (the hub's TTS) and the node plays the clip with its
        announcement handling (ducking, LED ring). ``start_conversation`` needs an Assist pipeline to
        hear the answer, which the hub does not run, so it is refused by name and callers fall back.
        """
        if service == "start_conversation":
            raise ServiceNotFound("assist_satellite.start_conversation needs a voice pipeline, which the hub "
                                  "does not run; use assist_satellite.announce")
        if service != "announce":
            raise ServiceNotFound(f"assist_satellite.{service} is not supported")
        client = self._require_client()
        media_id = str(data.get("media_id") or "")
        message = str(data.get("message") or "")
        if not media_id:
            if not message:
                raise HubError("assist_satellite.announce needs a message or a media_id")
            from services.homehub import tts

            media_id = await tts.make_clip(message)
        try:
            await client.send_voice_assistant_announcement_await_response(
                media_id, SATELLITE_ANNOUNCE_TIMEOUT_S, text=message,
                preannounce_media_id=str(data.get("preannounce_media_id") or ""))
        except APIConnectionError as exc:
            raise DeviceUnavailable(f"ESPHome {self.device_name}: {exc}") from exc
        except TimeoutError as exc:
            raise DeviceUnavailable(f"ESPHome {self.device_name} did not finish the announcement") from exc
        return None

    def _local_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        if entity.domain == "select" and service == "select_option":
            # Not the node's entity (HA's voice pipeline made it): its attributes are whatever was restored,
            # so the choice is remembered without passing them off as the device's (no publish).
            cur = self.hub.states.get(entity.entity_id)
            self.hub.states.set(entity.entity_id, str(data.get("option")), dict(cur.attributes) if cur else {})
            return None
        raise ServiceNotFound(f"{entity.entity_id} is a Home Assistant voice-pipeline entity; "
                              f"{domain}.{service} has nothing to drive")

    def _current(self, k: tuple[int, int]) -> Any:
        return self._last.get(k)

    def _send(self, c: Any, info: Any, service: str, data: dict[str, Any], k: tuple[int, int]) -> None:
        dev = getattr(info, "device_id", 0) or 0
        key = info.key
        cur = self._current(k)

        def resolve_toggle(on_name: str, off_name: str, is_on: bool) -> str:
            return off_name if is_on else on_name

        if isinstance(info, SwitchInfo):
            if service == "toggle":
                service = resolve_toggle("turn_on", "turn_off", bool(cur and cur.state))
            if service not in ("turn_on", "turn_off"):
                raise ServiceNotFound(f"switch.{service}")
            c.switch_command(key, service == "turn_on", device_id=dev)
        elif isinstance(info, LightInfo):
            if service == "toggle":
                service = resolve_toggle("turn_on", "turn_off", bool(cur and cur.state))
            if service == "turn_off":
                kw: dict[str, Any] = {"state": False}
                if data.get("transition") is not None:
                    kw["transition_length"] = float(data["transition"])
                c.light_command(key, device_id=dev, **kw)
            elif service == "turn_on":
                c.light_command(key, device_id=dev, **self._light_on_kwargs(info, data))
            else:
                raise ServiceNotFound(f"light.{service}")
        elif isinstance(info, ButtonInfo):
            if service != "press":
                raise ServiceNotFound(f"button.{service}")
            c.button_command(key, device_id=dev)
        elif isinstance(info, NumberInfo):
            if service != "set_value":
                raise ServiceNotFound(f"number.{service}")
            c.number_command(key, float(data["value"]), device_id=dev)
        elif isinstance(info, SelectInfo):
            if service == "select_option":
                option = str(data.get("option"))
                if info.options and option not in info.options:
                    raise HubError(f"{option!r} is not one of {list(info.options)}")
            elif service in ("select_next", "select_previous", "select_first", "select_last") and info.options:
                opts = list(info.options)
                i = opts.index(cur.state) if cur is not None and cur.state in opts else 0
                option = {"select_first": opts[0], "select_last": opts[-1],
                          "select_next": opts[(i + 1) % len(opts)],
                          "select_previous": opts[(i - 1) % len(opts)]}[service]
            else:
                raise ServiceNotFound(f"select.{service}")
            c.select_command(key, option, device_id=dev)
        elif isinstance(info, TextInfo):
            if service != "set_value":
                raise ServiceNotFound(f"text.{service}")
            c.text_command(key, str(data.get("value", "")), device_id=dev)
        elif isinstance(info, CoverInfo):
            if service == "toggle":
                service = resolve_toggle("open_cover", "close_cover", bool(cur and cur.position > 0))
            if service == "open_cover":
                c.cover_command(key, position=1.0, device_id=dev)
            elif service == "close_cover":
                c.cover_command(key, position=0.0, device_id=dev)
            elif service == "stop_cover":
                c.cover_command(key, stop=True, device_id=dev)
            elif service == "set_cover_position":
                c.cover_command(key, position=float(data["position"]) / 100, device_id=dev)
            elif service == "open_cover_tilt":
                c.cover_command(key, tilt=1.0, device_id=dev)
            elif service == "close_cover_tilt":
                c.cover_command(key, tilt=0.0, device_id=dev)
            elif service == "set_cover_tilt_position":
                c.cover_command(key, tilt=float(data["tilt_position"]) / 100, device_id=dev)
            else:
                raise ServiceNotFound(f"cover.{service}")
        elif isinstance(info, ValveInfo):
            if service == "toggle":
                service = resolve_toggle("open_valve", "close_valve", bool(cur and cur.position > 0))
            if service == "open_valve":
                c.valve_command(key, position=1.0, device_id=dev)
            elif service == "close_valve":
                c.valve_command(key, position=0.0, device_id=dev)
            elif service == "stop_valve":
                c.valve_command(key, stop=True, device_id=dev)
            elif service == "set_valve_position":
                c.valve_command(key, position=float(data["position"]) / 100, device_id=dev)
            else:
                raise ServiceNotFound(f"valve.{service}")
        elif isinstance(info, FanInfo):
            self._fan(c, info, service, data, cur, dev)
        elif isinstance(info, LockInfo):
            cmd = {"lock": LockCommand.LOCK, "unlock": LockCommand.UNLOCK, "open": LockCommand.OPEN}.get(service)
            if cmd is None:
                raise ServiceNotFound(f"lock.{service}")
            c.lock_command(key, cmd, code=data.get("code"), device_id=dev)
        elif isinstance(info, SirenInfo):
            if service == "toggle":
                service = resolve_toggle("turn_on", "turn_off", bool(cur and cur.state))
            if service == "turn_off":
                c.siren_command(key, state=False, device_id=dev)
            elif service == "turn_on":
                c.siren_command(key, state=True, tone=data.get("tone"),
                                volume=None if data.get("volume_level") is None else float(data["volume_level"]),
                                duration=None if data.get("duration") is None else int(data["duration"]),
                                device_id=dev)
            else:
                raise ServiceNotFound(f"siren.{service}")
        elif isinstance(info, MediaPlayerInfo):
            self._media(c, info, service, data, cur, dev)
        else:
            raise ServiceNotFound(f"{platform_of(info)}.{service} is not supported for ESPHome yet")

    def _light_on_kwargs(self, info: LightInfo, data: dict[str, Any]) -> dict[str, Any]:
        kw: dict[str, Any] = {"state": True}
        modes = [int(m) for m in self._light_modes(info)]
        if data.get("brightness") is not None:
            kw["brightness"] = max(0.0, min(1.0, float(data["brightness"]) / 255))
        elif data.get("brightness_pct") is not None:
            kw["brightness"] = max(0.0, min(1.0, float(data["brightness_pct"]) / 100))
        if kw.get("brightness") == 0.0:
            return {"state": False}
        if data.get("transition") is not None:
            kw["transition_length"] = float(data["transition"])
        if data.get("flash"):
            kw["flash_length"] = 10.0 if data["flash"] == "long" else 2.0
        rgb = data.get("rgb_color")
        if rgb is None and data.get("hs_color") is not None:
            h, s = data["hs_color"]
            r, g, b = colorsys.hsv_to_rgb(float(h) / 360, float(s) / 100, 1.0)
            rgb = [r * 255, g * 255, b * 255]
        kelvin = data.get("color_temp_kelvin") or data.get("kelvin")
        if kelvin is None and data.get("color_temp") is not None:
            kelvin = 1_000_000 / float(data["color_temp"])
        if rgb is not None:
            rgb_modes = [m for m in modes if m & LightColorCapability.RGB]
            if rgb_modes:
                # Prefer plain RGB so the white channels do not wash the colour out.
                kw["color_mode"] = min(rgb_modes, key=lambda m: bin(m).count("1"))
                top = max(float(x) for x in rgb)
                if top > 0:
                    kw["rgb"] = tuple(float(x) / top for x in rgb)
                    kw["color_brightness"] = top / 255
                else:
                    kw["rgb"] = (0.0, 0.0, 0.0)
        elif kelvin is not None and float(kelvin) > 0:
            ct_mask = LightColorCapability.COLOR_TEMPERATURE | LightColorCapability.COLD_WARM_WHITE
            ct_modes = [m for m in modes if m & ct_mask]
            if ct_modes:
                kw["color_mode"] = min(ct_modes, key=lambda m: (bool(m & LightColorCapability.RGB), m))
                mireds = 1_000_000 / float(kelvin)
                if info.min_mireds and info.max_mireds:
                    mireds = max(info.min_mireds, min(info.max_mireds, mireds))
                kw["color_temperature"] = mireds
        if data.get("effect") is not None:
            kw["effect"] = str(data["effect"])
        return kw

    def _fan(self, c: Any, info: FanInfo, service: str, data: dict[str, Any], cur: Any, dev: int) -> None:
        if service == "toggle":
            service = "turn_off" if cur is not None and cur.state else "turn_on"
        count = info.supported_speed_count or 0
        if service == "turn_off":
            c.fan_command(info.key, state=False, device_id=dev)
            return
        if service in ("turn_on", "set_percentage"):
            pct = data.get("percentage")
            if pct is not None and float(pct) <= 0:
                c.fan_command(info.key, state=False, device_id=dev)
                return
            kw: dict[str, Any] = {"state": True}
            if pct is not None and count:
                kw["speed_level"] = max(1, math.ceil(float(pct) / 100 * count))
            if data.get("preset_mode"):
                kw["preset_mode"] = str(data["preset_mode"])
            c.fan_command(info.key, device_id=dev, **kw)
        elif service == "oscillate":
            c.fan_command(info.key, oscillating=bool(data.get("oscillating")), device_id=dev)
        elif service == "set_direction":
            d = FanDirection.REVERSE if data.get("direction") == "reverse" else FanDirection.FORWARD
            c.fan_command(info.key, direction=d, device_id=dev)
        elif service == "set_preset_mode":
            c.fan_command(info.key, preset_mode=str(data.get("preset_mode")), device_id=dev)
        else:
            raise ServiceNotFound(f"fan.{service}")

    def _media(self, c: Any, info: MediaPlayerInfo, service: str, data: dict[str, Any], cur: Any,
               dev: int) -> None:
        simple = {
            "media_play": MediaPlayerCommand.PLAY, "media_pause": MediaPlayerCommand.PAUSE,
            "media_stop": MediaPlayerCommand.STOP, "media_play_pause": MediaPlayerCommand.TOGGLE,
            "volume_up": MediaPlayerCommand.VOLUME_UP, "volume_down": MediaPlayerCommand.VOLUME_DOWN,
            "turn_on": MediaPlayerCommand.TURN_ON, "turn_off": MediaPlayerCommand.TURN_OFF,
            "clear_playlist": MediaPlayerCommand.CLEAR_PLAYLIST,
        }
        if service == "toggle":
            off = cur is not None and cur.state == MediaPlayerState.OFF
            service = "turn_on" if off else "turn_off"
        if service in simple:
            c.media_player_command(info.key, command=simple[service], device_id=dev)
        elif service == "volume_set":
            c.media_player_command(info.key, volume=float(data["volume_level"]), device_id=dev)
        elif service == "volume_mute":
            cmd = MediaPlayerCommand.MUTE if data.get("is_volume_muted") else MediaPlayerCommand.UNMUTE
            c.media_player_command(info.key, command=cmd, device_id=dev)
        elif service == "repeat_set":
            cmd = MediaPlayerCommand.REPEAT_OFF if data.get("repeat") in (None, "off") else MediaPlayerCommand.REPEAT_ONE
            c.media_player_command(info.key, command=cmd, device_id=dev)
        elif service == "play_media":
            url = str(data.get("media_content_id") or "")
            if not url:
                raise HubError("play_media needs media_content_id")
            announce = data.get("announce")
            if data.get("enqueue") in ("add", "next"):
                c.media_player_command(info.key, command=MediaPlayerCommand.ENQUEUE, media_url=url, device_id=dev)
            else:
                c.media_player_command(info.key, media_url=url,
                                       announcement=bool(announce) if announce is not None else None,
                                       device_id=dev)
        else:
            raise ServiceNotFound(f"media_player.{service}")

    # -- user-defined actions on the node ---------------------------------------
    def _register_services(self, node_name: str, services: list[UserService]) -> None:
        self._unregister_services()
        prefix = (node_name or self.device_name).replace("-", "_")
        for svc in services:
            name = f"{prefix}_{svc.name}"
            schema = {a.name: {"name": a.name, "required": not getattr(a, "optional", False),
                               "description": getattr(a, "description", "")} for a in svc.args}
            self.hub.register_service("esphome", name, self._service_handler(svc), schema)
            self._services.append(("esphome", name))
        if services:
            self.log.info("ESPHome %s: registered %s", self.device_name,
                          ", ".join(f"esphome.{n}" for _, n in self._services))

    def _unregister_services(self) -> None:
        for key in self._services:
            self.hub.services.pop(key, None)
            self.hub.service_schemas.pop(key, None)
        self._services = []

    def _service_handler(self, svc: UserService):
        async def handler(_hub: Any, call: ServiceCall) -> Any:
            client = self._require_client()
            args: dict[str, Any] = {}
            for a in svc.args:
                if a.name in call.data:
                    args[a.name] = _convert_arg(a.type, call.data[a.name])
                elif not getattr(a, "optional", False):
                    raise HubError(f"esphome {svc.name} needs {a.name!r}")
            wants = call.return_response and int(getattr(svc, "supports_response", 0) or 0) != 0
            try:
                resp = await asyncio.wait_for(
                    client.execute_service(svc, args, return_response=True if wants else None),
                    SERVICE_TIMEOUT)
            except APIConnectionError as exc:
                raise DeviceUnavailable(f"ESPHome {self.device_name}: {exc}") from exc
            except TimeoutError as exc:
                raise DeviceUnavailable(f"ESPHome {self.device_name} did not answer {svc.name}") from exc
            if resp is None:
                return None
            if not getattr(resp, "success", True):
                raise HubError(f"esphome {svc.name} failed: {getattr(resp, 'error_message', '')}")
            raw = getattr(resp, "response_data", b"") or b""
            try:
                return json.loads(raw) if raw else {}
            except ValueError:
                return {"response": raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw}

        return handler

    # -- the node asking the hub ------------------------------------------------
    def _on_service_call(self, call: Any) -> None:
        if not self.allow_service_calls:
            self.log.info("ESPHome %s asked for %s, but allow_service_calls is off", self.device_name,
                          call.service)
            return
        data: dict[str, Any] = dict(call.data)
        for k, v in dict(call.data_template).items():
            if "{{" in v or "{%" in v:
                self.log.warning("ESPHome %s: template data for %s.%s is not rendered", self.device_name,
                                 call.service, k)
            data[k] = v
        if call.is_event:
            self.hub.bus.fire(call.service, {**data, "device_id": self.entities[0].device_id
                                             if self.entities else None})
            return
        domain, _, service = call.service.partition(".")
        if not service:
            self.log.warning("ESPHome %s asked for a malformed action %r", self.device_name, call.service)
            return

        async def run() -> None:
            try:
                await self.hub.call_service(domain, service, data)
            except Exception as exc:
                self.log.warning("ESPHome %s: action %s failed: %s", self.device_name, call.service, exc)

        task = asyncio.ensure_future(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_state_sub(self, entity_id: str, attribute: str | None) -> None:
        self._ha_subs.setdefault(entity_id, set()).add(attribute or None)
        self._send_ha_state(entity_id, attribute or None)

    def _on_state_request(self, entity_id: str, attribute: str | None) -> None:
        self._send_ha_state(entity_id, attribute or None)

    def _send_ha_state(self, entity_id: str, attribute: str | None) -> None:
        client = self._client
        st = self.hub.states.get(entity_id)
        if client is None or st is None:
            return
        if attribute:
            v = st.attributes.get(attribute)
            if v is None:
                return
            value = ("on" if v else "off") if isinstance(v, bool) else str(v)
        else:
            value = st.state
        with contextlib.suppress(APIConnectionError):
            client.send_home_assistant_state(entity_id, attribute, value)

    def _on_hub_state(self, _event: str, ev: dict[str, Any]) -> None:
        eid = ev.get("entity_id")
        attrs = self._ha_subs.get(eid) if eid else None
        if not attrs or ev.get("new_state") is None:
            return
        for attribute in list(attrs):
            self._send_ha_state(eid, attribute)
