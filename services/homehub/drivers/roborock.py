"""Roborock vacuums through python-roborock, with the cloud login HA kept.

``entry.data`` = ``{base_url, user_data, username}``; ``user_data`` is the
account's login token data (``UserData``) exactly as python-roborock returned it
to HA, so no new login (and no emailed code) is needed. The library fetches the
home's device list once from the cloud, then talks to each vacuum on the LAN
with the device's local key and falls back to the cloud MQTT broker only when
the LAN connection fails.

Entities, by unique_id (HA's roborock integration):
- ``{duid}``           the vacuum (HA lower-cases the duid in unique_ids)
- ``{key}_{duid}``     a sensor, binary sensor, select, number, switch or time
- ``{duid}_map_...``   map images (not drawn here; marked unavailable)

Status is polled every 30 s and also pushed by the device when it changes;
consumables, totals, volume and do-not-disturb change slowly and are re-read
every five minutes.

What each entity is comes from the vacuum and this driver, never from the
imported registry: the device's own mappings give the fan speeds, mop
intensities, mop routes and maps; ``SENSOR_INFO`` gives each reading's class,
unit and state class; the device record (``product.model``, ``device_info.fv`` /
``sn``) gives the model, firmware and serial (``confirm_device``).
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from services.homehub.core import UNKNOWN, DeviceUnavailable, Driver, EntityEntry, HubError, ServiceNotFound

try:  # guarded so the tests can run the driver against fake devices
    from roborock.data import UserData
    from roborock.devices.cache import InMemoryCache
    from roborock.devices.device_manager import UserParams, create_device_manager
    from roborock.exceptions import RoborockException
except ImportError:  # pragma: no cover - the image installs python-roborock
    UserData = InMemoryCache = UserParams = create_device_manager = None  # type: ignore[assignment,misc]

    class RoborockException(Exception):  # type: ignore[no-redef]
        pass

SETUP_TIMEOUT = 90.0
COMMAND_TIMEOUT = 20.0
SLOW_REFRESH_S = 300.0

# HA's roborock vacuum: device state name -> vacuum activity.
STATE_TO_ACTIVITY = {
    "starting": "idle", "charger_disconnected": "idle", "idle": "idle", "shutting_down": "idle",
    "remote_control_active": "cleaning", "cleaning": "cleaning", "manual_mode": "cleaning",
    "spot_cleaning": "cleaning", "going_to_target": "cleaning", "zoned_cleaning": "cleaning",
    "segment_cleaning": "cleaning", "mapping": "cleaning", "patrol": "cleaning",
    "returning_home": "returning", "docking": "returning", "going_to_wash_the_mop": "returning",
    "charging": "docked", "updating": "docked", "emptying_the_bin": "docked", "washing_the_mop": "docked",
    "charging_complete": "docked", "attaching_the_mop": "docked", "detaching_the_mop": "docked",
    "air_drying_stopping": "docked", "back_to_dock_washing_duster": "docked",
    "paused": "paused",
    "charging_problem": "error", "error": "error", "device_offline": "error", "locked": "error",
}

#: HA's VacuumEntityFeature bits for what this driver serves: pause, stop, return home, fan speed, send
#: command, locate, clean spot, state, start, and clean area (segment cleaning through send_command).
VACUUM_FEATURES = 4 | 8 | 16 | 32 | 256 | 512 | 1024 | 4096 | 8192 | 16384
#: Each sensor key's device class, unit and state class (the readings python-roborock returns).
SENSOR_INFO: dict[str, dict[str, Any]] = {
    "battery": {"device_class": "battery", "unit_of_measurement": "%", "state_class": "measurement"},
    "cleaning_time": {"device_class": "duration", "unit_of_measurement": "s"},
    "cleaning_area": {"unit_of_measurement": "m²"},
    "total_cleaning_time": {"device_class": "duration", "unit_of_measurement": "s",
                            "state_class": "total_increasing"},
    "total_cleaning_area": {"unit_of_measurement": "m²", "state_class": "total_increasing"},
    "total_cleaning_count": {"state_class": "total_increasing"},
    "last_clean_start": {"device_class": "timestamp"},
    "last_clean_end": {"device_class": "timestamp"},
    **{k: {"device_class": "duration", "unit_of_measurement": "s"} for k in (
        "main_brush_time_left", "side_brush_time_left", "filter_time_left", "sensor_time_left",
        "strainer_time_left", "dust_collection_time_left", "cleaning_brush_time_left", "mop_roller_time_left")},
    "status": {"device_class": "enum"},
}

VACUUM_COMMANDS = {
    "start": "app_start", "pause": "app_pause", "stop": "app_stop",
    "return_to_base": "app_charge", "locate": "find_me", "clean_spot": "app_spot",
}


def _get(obj: Any, *path: str) -> Any:
    for name in path:
        if obj is None:
            return None
        try:
            obj = getattr(obj, name)
        except Exception:
            return None
    return obj


def _iso(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


def _hhmm(hour: Any, minute: Any) -> str | None:
    if hour is None or minute is None:
        return None
    return f"{int(hour):02d}:{int(minute):02d}:00"


def _bool(v: Any) -> str:
    return UNKNOWN if v is None else ("on" if v else "off")


def activity(state_name: str | None) -> str:
    if not state_name:
        return UNKNOWN
    if state_name in STATE_TO_ACTIVITY:
        return STATE_TO_ACTIVITY[state_name]
    if "mopping" in state_name or "cleaning" in state_name:
        return "cleaning"
    return "idle"


def _charging(props: Any) -> Any:
    try:
        return props.status.is_battery_charging
    except Exception:
        name = _get(props, "status", "state_name")
        return None if name is None else name == "charging"


def _current_map_name(props: Any) -> str | None:
    info = _get(props, "maps", "current_map_info")
    if info is not None:
        return info.name or f"Map {info.map_flag}"
    flag = _get(props, "maps", "current_map")
    return None if flag is None else f"Map {flag}"


# key -> how to read the value from the device's v1 properties
SENSORS: dict[str, Any] = {
    "status": lambda p: _get(p, "status", "state_name"),
    "battery": lambda p: _get(p, "status", "battery"),
    "cleaning_time": lambda p: _get(p, "status", "clean_time"),
    "cleaning_area": lambda p: _get(p, "status", "square_meter_clean_area"),
    "vacuum_error": lambda p: _get(p, "status", "error_code_name"),
    "total_cleaning_time": lambda p: _get(p, "clean_summary", "clean_time"),
    "total_cleaning_area": lambda p: _get(p, "clean_summary", "square_meter_clean_area"),
    "total_cleaning_count": lambda p: _get(p, "clean_summary", "clean_count"),
    "last_clean_start": lambda p: _iso(_get(p, "clean_summary", "last_clean_record", "begin_datetime")),
    "last_clean_end": lambda p: _iso(_get(p, "clean_summary", "last_clean_record", "end_datetime")),
    "main_brush_time_left": lambda p: _get(p, "consumables", "main_brush_time_left"),
    "side_brush_time_left": lambda p: _get(p, "consumables", "side_brush_time_left"),
    "filter_time_left": lambda p: _get(p, "consumables", "filter_time_left"),
    "sensor_time_left": lambda p: _get(p, "consumables", "sensor_time_left"),
    "strainer_time_left": lambda p: _get(p, "consumables", "strainer_time_left"),
    "dust_collection_time_left": lambda p: _get(p, "consumables", "dust_collection_time_left"),
    "cleaning_brush_time_left": lambda p: _get(p, "consumables", "cleaning_brush_time_left"),
    "mop_roller_time_left": lambda p: _get(p, "consumables", "mop_roller_time_left"),
}
BINARY_SENSORS: dict[str, Any] = {
    "water_box_carriage_status": lambda p: _get(p, "status", "water_box_carriage_status"),
    "water_box_status": lambda p: _get(p, "status", "water_box_status"),
    "water_shortage": lambda p: _get(p, "status", "water_shortage_status"),
    "in_cleaning": lambda p: _get(p, "status", "in_cleaning"),
    "battery_charging": _charging,
}
SELECTS: dict[str, Any] = {
    "water_box_mode": lambda p: _get(p, "status", "water_mode_name"),
    "mop_mode": lambda p: _get(p, "status", "mop_route_name"),
    "cleaning_mode": lambda p: _get(p, "status", "current_cleaning_mode_name"),
    "selected_map": _current_map_name,
}
TIMES: dict[str, Any] = {
    "dnd_start_time": lambda p: _hhmm(_get(p, "dnd", "start_hour"), _get(p, "dnd", "start_minute")),
    "dnd_end_time": lambda p: _hhmm(_get(p, "dnd", "end_hour"), _get(p, "dnd", "end_minute")),
}


class RoborockDriver(Driver):
    platform = "roborock"
    poll_interval = 30.0

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self.manager: Any = None
        self.devices: dict[str, Any] = {}  # lower-cased duid -> RoborockDevice
        self._slow_at: dict[str, float] = {}
        self._online: dict[str, bool] = {}
        self._unsubs: list[Any] = []

    # -- setup ---------------------------------------------------------------
    async def _create_manager(self) -> Any:
        if create_device_manager is None:
            raise HubError("python-roborock is not installed")
        data = self.entry.data
        params = UserParams(username=data["username"], user_data=UserData.from_dict(data["user_data"]),
                            base_url=data.get("base_url"))
        return await create_device_manager(params, cache=InMemoryCache())

    async def async_setup(self) -> None:
        try:
            self.manager = await asyncio.wait_for(self._create_manager(), SETUP_TIMEOUT)
        except RoborockException as exc:
            self.log.error("Roborock cloud login with the stored token failed for %s: %s "
                           "(if this persists the account must be signed in again)", self.entry.title, exc)
            raise
        except TimeoutError as exc:
            raise DeviceUnavailable("Roborock cloud did not answer in time") from exc
        devices = await self.manager.get_devices()
        self.devices = {d.duid.lower(): d for d in devices if getattr(d, "v1_properties", None) is not None}
        if not self.devices:
            raise HubError("no supported Roborock vacuum on this account")
        for duid, dev in self.devices.items():
            status = dev.v1_properties.status
            with contextlib.suppress(Exception):
                self._unsubs.append(status.add_update_listener(lambda d=duid: self.publish_device(d)))
            await self.refresh_device(duid, slow=True)

    async def async_unload(self) -> None:
        for unsub in self._unsubs:
            with contextlib.suppress(Exception):
                unsub()
        self._unsubs.clear()
        if self.manager is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.manager.close(), 10)

    async def async_poll(self) -> None:
        for duid in list(self.devices):
            slow = time.monotonic() >= self._slow_at.get(duid, 0)
            await self.refresh_device(duid, slow=slow)

    async def refresh_device(self, duid: str, slow: bool = False) -> None:
        props = self.devices[duid].v1_properties
        try:
            await asyncio.wait_for(props.status.refresh(), COMMAND_TIMEOUT)
        except Exception as exc:
            if self._online.get(duid, True):
                self.log.warning("Roborock %s did not answer: %s", duid, exc)
            self._online[duid] = False
            self.mark_unavailable(self.device_entities(duid))
            return
        if not self._online.get(duid, False):
            self.confirm_device_info(duid)
        self._online[duid] = True
        if slow:
            self._slow_at[duid] = time.monotonic() + SLOW_REFRESH_S
            for name in ("consumables", "clean_summary", "sound_volume", "dnd", "maps"):
                trait = getattr(props, name, None)
                if trait is None:
                    continue
                try:
                    await asyncio.wait_for(trait.refresh(), COMMAND_TIMEOUT)
                except Exception as exc:  # one slow trait must not hide the status
                    self.log.debug("Roborock %s %s refresh failed: %s", duid, name, exc)
        self.publish_device(duid)

    # -- entities ------------------------------------------------------------
    def split(self, entity: EntityEntry) -> tuple[str, str] | None:
        """unique_id -> (key, duid); key '' is the vacuum, 'map' an image."""
        uid = entity.unique_id.lower()
        for duid in self.devices:
            if uid == duid:
                return "", duid
            if uid.startswith(duid + "_map_"):
                return "map", duid
            if uid.endswith("_" + duid):
                return uid[: -len(duid) - 1], duid
        return None

    def device_entities(self, duid: str) -> list[EntityEntry]:
        return [e for e in self.entities if (s := self.split(e)) is not None and s[1] == duid]

    def publish_device(self, duid: str) -> None:
        if not self._online.get(duid, False):
            return
        props = self.devices[duid].v1_properties
        for ent in self.device_entities(duid):
            key = self.split(ent)[0]  # type: ignore[index]
            try:
                self.publish_entity(ent, key, props)
            except Exception:
                self.log.exception("publishing %s failed", ent.entity_id)

    def confirm_device_info(self, duid: str) -> None:
        """Model, firmware and serial as the account's device record has them."""
        dev = self.devices.get(duid)
        ents = self.device_entities(duid)
        if dev is None or not ents:
            return
        product = getattr(dev, "product", None)
        info = getattr(dev, "device_info", None)
        for dev_id in {e.device_id for e in ents if e.device_id}:
            self.confirm_device(dev_id, manufacturer="Roborock", model=getattr(product, "model", None),
                                sw_version=getattr(info, "fv", None), serial_number=getattr(info, "sn", None),
                                identifiers=[["roborock", getattr(dev, "duid", duid)]])

    @staticmethod
    def _options(key: str, props: Any) -> list[str]:
        """A select's options from the device's own mappings."""
        status = props.status
        if key == "water_box_mode":
            return list((_get(status, "water_mode_mapping") or {}).values())
        if key == "mop_mode":
            return list((_get(status, "mop_route_mapping") or {}).values())
        if key == "cleaning_mode":
            current = _get(status, "current_cleaning_mode_name")
            known = [m for m in (_get(status, "cleaning_mode_options") or [])]
            names = [getattr(m, "value", m) for m in known]
            return names or ([current] if current else [])
        if key == "selected_map":
            return [info.name or f"Map {info.map_flag}" for info in _get(props, "maps", "map_info") or []]
        return []

    def publish_entity(self, ent: EntityEntry, key: str, props: Any) -> None:
        attrs: dict[str, Any] = {}
        if ent.domain == "vacuum" and key == "":
            status = props.status
            speeds = list((_get(status, "fan_speed_mapping") or {}).values()) or [
                getattr(m, "value", m) for m in _get(status, "fan_speed_options") or []]
            attrs.update({
                "fan_speed_list": speeds,
                "battery_level": _get(status, "battery"),
                "fan_speed": _get(status, "fan_speed_name"),
                "supported_features": VACUUM_FEATURES,
            })
            self.publish(ent, activity(_get(status, "state_name")), attrs)
        elif ent.domain == "sensor" and key in SENSORS:
            attrs.update(SENSOR_INFO.get(key, {}))
            if key == "status":
                attrs["options"] = sorted(STATE_TO_ACTIVITY)
            value = SENSORS[key](props)
            self.publish(ent, UNKNOWN if value is None else value, attrs)
        elif ent.domain == "sensor" and key == "current_room":
            # Needs the parsed map to place the robot in a room; not drawn here.
            self.publish(ent, UNKNOWN, attrs)
        elif ent.domain == "binary_sensor" and key in BINARY_SENSORS:
            self.publish(ent, _bool(BINARY_SENSORS[key](props)), attrs)
        elif ent.domain == "select" and key in SELECTS:
            attrs["options"] = self._options(key, props)
            value = SELECTS[key](props)
            self.publish(ent, UNKNOWN if value is None else value, attrs)
        elif ent.domain == "number" and key == "volume":
            attrs.update({"min": 0, "max": 100, "step": 1, "mode": "slider", "unit_of_measurement": "%"})
            value = _get(props, "sound_volume", "volume")
            self.publish(ent, UNKNOWN if value is None else value, attrs)
        elif ent.domain == "switch" and key == "dnd_switch":
            enabled = _get(props, "dnd", "enabled")
            self.publish(ent, _bool(None if enabled is None else enabled == 1), attrs)
        elif ent.domain == "time" and key in TIMES:
            value = TIMES[key](props)
            self.publish(ent, UNKNOWN if value is None else value, attrs)
        else:
            self.mark_unavailable([ent])

    # -- services ------------------------------------------------------------
    async def _send(self, props: Any, command: str, params: Any = None) -> Any:
        try:
            return await asyncio.wait_for(props.command.send(command, params=params), COMMAND_TIMEOUT)
        except TimeoutError as exc:
            raise DeviceUnavailable(f"vacuum did not answer {command}") from exc
        except RoborockException as exc:
            raise DeviceUnavailable(f"vacuum refused {command}: {exc}") from exc

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        split = self.split(entity)
        if split is None:
            raise ServiceNotFound(f"cannot map {entity.entity_id} to a Roborock device")
        key, duid = split
        props = self.devices[duid].v1_properties
        result: Any = None
        if entity.domain == "vacuum" and key == "":
            result = await self._vacuum_call(props, service, data)
        elif entity.domain == "select" and service == "select_option":
            await self._select(props, key, str(data.get("option", "")))
        elif entity.domain == "number" and key == "volume" and service == "set_value":
            await asyncio.wait_for(props.sound_volume.set_volume(int(float(data["value"]))), COMMAND_TIMEOUT)
        elif entity.domain == "switch" and key == "dnd_switch" and service in ("turn_on", "turn_off", "toggle"):
            on = service == "turn_on" or (service == "toggle" and _get(props, "dnd", "enabled") != 1)
            await asyncio.wait_for(props.dnd.enable() if on else props.dnd.disable(), COMMAND_TIMEOUT)
        else:
            raise ServiceNotFound(f"roborock cannot run {domain}.{service} on {entity.entity_id}")
        await self.refresh_device(duid, slow=entity.domain != "vacuum")
        return result

    async def _vacuum_call(self, props: Any, service: str, data: dict[str, Any]) -> Any:
        if service in VACUUM_COMMANDS:
            return await self._send(props, VACUUM_COMMANDS[service])
        if service == "set_fan_speed":
            wanted = str(data.get("fan_speed", ""))
            mapping = _get(props, "status", "fan_speed_mapping") or {}
            codes = {name: code for code, name in mapping.items()}
            if wanted not in codes:
                raise HubError(f"fan_speed must be one of {sorted(codes)}")
            return await self._send(props, "set_custom_mode", [codes[wanted]])
        if service == "send_command":
            command = data.get("command")
            if not command:
                raise HubError("send_command needs a command")
            return await self._send(props, str(command), data.get("params"))
        raise ServiceNotFound(f"roborock cannot run vacuum.{service}")

    async def _select(self, props: Any, key: str, option: str) -> None:
        status = props.status
        if key == "water_box_mode":
            codes = {name: code for code, name in (_get(status, "water_mode_mapping") or {}).items()}
            if option not in codes:
                raise HubError(f"option must be one of {sorted(codes)}")
            await self._send(props, "set_water_box_custom_mode", [codes[option]])
        elif key == "mop_mode":
            codes = {name: code for code, name in (_get(status, "mop_route_mapping") or {}).items()}
            if option not in codes:
                raise HubError(f"option must be one of {sorted(codes)}")
            await self._send(props, "set_mop_mode", [codes[option]])
        elif key == "cleaning_mode":
            await asyncio.wait_for(status.set_cleaning_mode(option), COMMAND_TIMEOUT)
        elif key == "selected_map":
            for info in _get(props, "maps", "map_info") or []:
                if option in (info.name, f"Map {info.map_flag}"):
                    await asyncio.wait_for(props.maps.set_current_map(info.map_flag), COMMAND_TIMEOUT)
                    return
            raise HubError(f"no map named {option!r}")
        else:
            raise ServiceNotFound(f"roborock cannot set {key}")
