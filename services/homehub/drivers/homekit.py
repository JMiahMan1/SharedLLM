"""HomeKit accessories over IP (HAP), with the pairing Home Assistant made.

One HA config entry is one paired accessory (or bridge). ``entry.data`` is
aiohomekit's own pairing dict (AccessoryPairingID, AccessoryLTPK, iOSDeviceLTSK,
...), stored by HA unchanged or made by ``services/homehub/pairing.py``, so it is
handed to aiohomekit as-is and no re-pairing is needed.

Thermostats are handled here; every other standard HAP service (lights,
switches, outlets, fans, covers, locks, sensors, security systems, buttons) is
mapped by type in ``homekit_services.py``.

Entities, by unique_id (HA's homekit_controller):
- ``{pairing_id}_{aid}_{service_iid}``             the climate entity (thermostat service)
- ``{pairing_id}_{aid}_{service_iid}_{char_iid}``  one characteristic: a temperature or
  humidity sensor, the display-units select, ecobee's mode select, an identify or
  clear-hold button

HomeKit always speaks Celsius. HA showed these in the household's unit (US, so
Fahrenheit), with climate values rounded to whole degrees the way HA's climate
entity does for Fahrenheit, so this driver converts and rounds the same way and
says which unit it used in ``temperature_unit``.

State arrives as HAP events (the accessory pushes changes on the open session);
a 60 s poll reads everything again in case an event was lost or the session
dropped. If the accessory's address changed, aiohomekit's zeroconf browser
finds it again by its pairing id and the next reconnect uses the new address.

What each accessory is comes from the accessory on every connect: its
AccessoryInformation service (maker, model, serial, firmware, hardware) is
confirmed onto its device, and every entity's class, unit, state class, options
and event types are recomputed from its services and characteristics
(``homekit_services.entity_specs``, the same function pairing registers with)
and confirmed onto the registry. The publishers read those specs
(``spec_for``), never an imported registry value.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from services.homehub.core import UNKNOWN, DeviceUnavailable, Driver, EntityEntry, HubError, ServiceNotFound
from services.homehub.drivers import homekit_services as hks

try:  # guarded so the tests can run the driver against a fake pairing
    from aiohomekit.characteristic_cache import CharacteristicCacheMemory
    from aiohomekit.controller.ip.controller import IpController
except ImportError:  # pragma: no cover - the image installs aiohomekit
    CharacteristicCacheMemory = None  # type: ignore[assignment,misc]
    IpController = None  # type: ignore[assignment,misc]

log = logging.getLogger("homehub.homekit_controller")

REQUEST_TIMEOUT = 20.0
#: An accessory closes an idle connection now and then (a Honeywell T5 every half hour or so):
#: the first read after that fails and the library reconnects on the next one. One retry
#: after this pause tells a closed connection from an accessory that has gone away.
POLL_RETRY_PAUSE_S = 1.0

# Characteristic and service types, as the short form of the HAP UUID (the
# first segment without leading zeros). Vendor types keep their first segment.
SVC_THERMOSTAT = "4A"
CH_IDENTIFY = "14"
CH_CURRENT_STATE = "F"        # heating/cooling current: 0 off, 1 heat, 2 cool
CH_TARGET_STATE = "33"        # heating/cooling target: 0 off, 1 heat, 2 cool, 3 auto
CH_CURRENT_TEMP = "11"
CH_TARGET_TEMP = "35"
CH_DISPLAY_UNITS = "36"       # 0 celsius, 1 fahrenheit
CH_COOL_THRESHOLD = "D"
CH_HEAT_THRESHOLD = "12"
CH_CURRENT_HUMIDITY = "10"
CH_TARGET_HUMIDITY = "34"
CH_FAN_TARGET = "BF"          # fan state target: 0 manual (on), 1 auto
CH_FAN_CURRENT = "AF"         # fan state current: 0 inactive, 1 idle, 2 blowing
ECOBEE_CURRENT_MODE = "B7DDB9A3"
ECOBEE_SET_HOLD_SCHEDULE = "1B300BC2"
ECOBEE_CLEAR_HOLD = "FA128DE6"

MODE_TO_HA = {0: "off", 1: "heat", 2: "cool", 3: "heat_cool"}
MODE_FROM_HA = {v: k for k, v in MODE_TO_HA.items()}
ACTION_TO_HA = {0: "idle", 1: "heating", 2: "cooling"}
ECOBEE_MODES = {0: "home", 1: "sleep", 2: "away"}
DISPLAY_UNITS = {0: "celsius", 1: "fahrenheit"}

# HA ClimateEntityFeature bits
TARGET_TEMPERATURE = 1
TARGET_TEMPERATURE_RANGE = 2
TARGET_HUMIDITY = 4
FAN_MODE = 8
TURN_OFF = 128
TURN_ON = 256

# One zeroconf instance (and _hap._tcp browser) for every paired accessory in the process.
HAP_TCP = "_hap._tcp.local."
_zeroconf: Any = None
_browser: Any = None
_zeroconf_lock = asyncio.Lock()


def short_type(uuid: str) -> str:
    """'0000004A-0000-1000-8000-0026BB765291' and '4A' both become '4A'."""
    head = str(uuid).split("-", 1)[0].upper()
    return head.lstrip("0") or "0"


def c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def f_to_c(f: float) -> float:
    return (f - 32.0) * 5.0 / 9.0


def parse_unique_id(unique_id: str) -> tuple[str, int, int, int | None] | None:
    """``pid_aid_sid[_iid]`` -> (pid, aid, service_iid, char_iid or None)."""
    parts = unique_id.split("_")
    try:
        if len(parts) == 3:
            return parts[0], int(parts[1]), int(parts[2]), None
        if len(parts) == 4:
            return parts[0], int(parts[1]), int(parts[2]), int(parts[3])
    except ValueError:
        return None
    return None


async def _shared_zeroconf() -> Any:
    """An AsyncZeroconf for rediscovery, or a stand-in when multicast is unavailable.

    The stand-in has ``zeroconf = None``, which aiohomekit's controller treats as
    "no browsing": the stored address is used and nothing is rediscovered.
    """
    global _zeroconf, _browser
    async with _zeroconf_lock:
        if _zeroconf is None:
            try:
                from zeroconf import ServiceListener
                from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf

                class _Quiet(ServiceListener):
                    def add_service(self, *_a) -> None: ...
                    def remove_service(self, *_a) -> None: ...
                    def update_service(self, *_a) -> None: ...

                azc = AsyncZeroconf()
                # aiohomekit does not browse by itself: it hooks onto a running
                # browser for _hap._tcp (HA's zeroconf integration provides one).
                _browser = AsyncServiceBrowser(azc.zeroconf, [HAP_TCP, "_hap._udp.local."], listener=_Quiet())
                _zeroconf = azc
            except Exception as exc:  # no multicast in this network namespace
                log.warning("zeroconf unavailable, HomeKit accessories will not be rediscovered: %s", exc)
                _zeroconf = SimpleNamespace(zeroconf=None)
        return _zeroconf


class HomeKitDriver(Driver):
    platform = "homekit_controller"
    poll_interval = 60.0

    def __init__(self, hub, entry) -> None:
        super().__init__(hub, entry)
        self._controller: Any = None
        self._pairing: Any = None
        self._unsubs: list[Any] = []
        self.chars: dict[tuple[int, int], dict[str, Any]] = {}       # (aid, iid) -> characteristic metadata
        self.services: dict[tuple[int, int], dict[str, int]] = {}    # (aid, service iid) -> type -> char iid
        self.service_of: dict[tuple[int, int], tuple[int, int]] = {}  # (aid, char iid) -> (aid, service iid)
        self.values: dict[tuple[int, int], Any] = {}
        self.available = False
        self._last_on_mode: dict[tuple[int, int], int] = {}
        #: (aid, service iid) -> (ISO time, event type) of a stateless switch's last press.
        self.event_stamps: dict[tuple[int, int], tuple[str, str]] = {}
        #: (aid, service iid, char iid or None) -> the registry spec the accessory's own layout gives.
        self.specs: dict[tuple[int, int, int | None], dict[str, Any]] = {}

    # -- setup --------------------------------------------------------------
    async def _make_pairing(self) -> Any:
        if IpController is None:
            raise HubError("aiohomekit is not installed")
        zc = await _shared_zeroconf()
        if self.entry.data.get("Connection") == "CoAP":  # HomeKit over Thread
            from services.homehub import homekit_thread

            self._controller, pairing = await homekit_thread.load_pairing(self.entry, zc)
            return pairing
        self._controller = IpController(char_cache=CharacteristicCacheMemory(), zeroconf_instance=zc)
        try:
            await self._controller.async_start()
        except Exception as exc:  # rediscovery is a nicety; the stored address still works
            self.log.warning("%s: zeroconf browsing unavailable, using the stored address: %s", self.entry.title, exc)
            self._controller = IpController(char_cache=CharacteristicCacheMemory(),
                                            zeroconf_instance=SimpleNamespace(zeroconf=None))
            await self._controller.async_start()
        pairing = self._controller.load_pairing(self.entry.entry_id, dict(self.entry.data))
        if pairing is None:
            raise HubError("pairing data is not an IP pairing")
        return pairing

    async def async_setup(self) -> None:
        data = self.entry.data
        if data.get("Connection", "IP") not in ("IP", "CoAP"):
            raise HubError(f"{data.get('Connection')} HomeKit transport is not supported, only IP and CoAP (Thread)")
        self._pairing = await self._make_pairing()
        try:
            accessories = await asyncio.wait_for(
                self._pairing.list_accessories_and_characteristics(), REQUEST_TIMEOUT)
        except Exception as exc:
            raise DeviceUnavailable(f"{self.entry.title}: accessory did not answer: {exc}") from exc
        self.index(accessories)
        self.confirm_from_accessories(accessories)
        self.available = True
        self._unsubs.append(self._pairing.dispatcher_connect(self._on_event))
        with contextlib.suppress(AttributeError):
            self._unsubs.append(self._pairing.dispatcher_availability_changed(self._on_availability))
        events = [k for k, c in self.chars.items() if "ev" in c["perms"]]
        if events:
            try:
                await asyncio.wait_for(self._pairing.subscribe(events), REQUEST_TIMEOUT)
            except Exception as exc:  # polling still covers it
                self.log.info("%s: event subscription failed, polling only: %s", self.entry.title, exc)
        self.publish_all()

    def index(self, accessories: list[dict[str, Any]]) -> None:
        """Learn the accessory layout and the values that came with it."""
        self.chars.clear()
        self.services.clear()
        self.service_of.clear()
        for acc in accessories:
            aid = int(acc["aid"])
            for svc in acc.get("services", []):
                skey = (aid, int(svc["iid"]))
                types: dict[str, int] = {"_service": short_type(svc["type"])}  # type: ignore[dict-item]
                for ch in svc.get("characteristics", []):
                    iid = int(ch["iid"])
                    t = short_type(ch["type"])
                    types.setdefault(t, iid)
                    self.chars[(aid, iid)] = {
                        "type": t, "perms": list(ch.get("perms") or []), "unit": ch.get("unit"),
                        "min": ch.get("minValue"), "max": ch.get("maxValue"), "step": ch.get("minStep"),
                        "valid": ch.get("valid-values"), "format": ch.get("format"),
                    }
                    self.service_of[(aid, iid)] = skey
                    if "value" in ch and "pr" in (ch.get("perms") or []):
                        self.values[(aid, iid)] = ch["value"]
                self.services[skey] = types

    def confirm_from_accessories(self, accessories: list[dict[str, Any]]) -> None:
        """What the accessories say about themselves, onto the registry as the device's own."""
        pid = str(self.entry.data.get("AccessoryPairingID") or self.entry.unique_id or "")
        self.specs.clear()
        for spec in hks.entity_specs(pid, accessories):
            parsed = parse_unique_id(str(spec["unique_id"]))
            if parsed is not None:
                self.specs[parsed[1:]] = spec
        infos = {int(acc["aid"]): hks.accessory_info(acc) for acc in accessories}
        for ent in self.entities:
            parsed = parse_unique_id(ent.unique_id or "")
            if parsed is None:
                continue
            info = infos.get(parsed[1]) or {}
            if ent.device_id and info:
                self.confirm_device(ent.device_id, **{k: info.get(k) for k in (
                    "manufacturer", "model", "serial_number", "sw_version", "hw_version")})
            spec = self.specs.get(parsed[1:])
            if spec is not None:
                fields = {k: spec[k] for k in ("original_device_class", "unit_of_measurement", "capabilities",
                                               "entity_category") if k in spec}
                if spec.get("name"):
                    fields["original_name"] = spec["name"]
                self.confirm_entity(ent, **fields)

    def spec_for(self, ent: EntityEntry) -> dict[str, Any]:
        """The registry spec the accessory's layout gives this entity (empty when it models none)."""
        parsed = parse_unique_id(ent.unique_id or "")
        return (self.specs.get(parsed[1:]) if parsed is not None else None) or {}

    # -- events and polling --------------------------------------------------
    def _on_event(self, event: dict[tuple[int, int], dict[str, Any]]) -> None:
        hks.note_events(self, event)
        for key, item in (event or {}).items():
            if isinstance(item, dict) and "value" in item:
                self.values[tuple(key)] = item["value"]  # type: ignore[index]
        if event:
            self.available = True
        self.publish_all()

    def _on_availability(self, available: bool) -> None:
        if not available:
            self.available = False
            self.mark_unavailable()

    async def async_poll(self) -> None:
        readable = [k for k, c in self.chars.items() if "pr" in c["perms"]]
        if not readable or self._pairing is None:
            return
        try:
            result = await self._read(readable)
        except Exception as exc:
            if self.available:
                self.log.warning("%s: accessory stopped answering: %s", self.entry.title, exc)
            self.available = False
            self.mark_unavailable()
            return
        for key, item in result.items():
            if isinstance(item, dict) and "value" in item:
                self.values[tuple(key)] = item["value"]  # type: ignore[index]
        self.available = True
        self.publish_all()

    async def _read(self, readable: list) -> dict:
        """Read once, and once more after a pause: the first read after the accessory closed
        its connection fails while the library reconnects, which is not the accessory gone."""
        try:
            return await asyncio.wait_for(self._pairing.get_characteristics(readable), REQUEST_TIMEOUT)
        except Exception as exc:
            self.log.debug("%s: read failed, retrying once: %s", self.entry.title, exc)
        await asyncio.sleep(POLL_RETRY_PAUSE_S)
        return await asyncio.wait_for(self._pairing.get_characteristics(readable), REQUEST_TIMEOUT)

    async def async_unpair(self) -> None:
        """Remove our pairing from the accessory, so it can be paired again (by us or anyone)."""
        pairing = self._pairing or await self._make_pairing()
        controller_id = self.entry.data.get("iOSPairingId")
        if not controller_id:
            raise HubError("the stored pairing has no controller id (iOSPairingId)")
        await asyncio.wait_for(pairing.remove_pairing(controller_id), REQUEST_TIMEOUT)

    async def async_unload(self) -> None:
        for unsub in self._unsubs:
            with contextlib.suppress(Exception):
                unsub()
        self._unsubs.clear()
        if self._pairing is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._pairing.close(), 5)
        if self._controller is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._controller.async_stop(), 5)

    # -- helpers -------------------------------------------------------------
    @property
    def fahrenheit(self) -> bool:
        return (self.hub.location.get("unit_system") or "us_customary") != "metric"

    @property
    def temp_unit(self) -> str:
        return "°F" if self.fahrenheit else "°C"

    def to_user(self, celsius: Any, whole: bool = False) -> float | int | None:
        """Celsius from the accessory to what HA showed. Climate rounds whole in F, tenths in C."""
        if celsius is None:
            return None
        v = c_to_f(float(celsius)) if self.fahrenheit else float(celsius)
        return round(v) if whole and self.fahrenheit else round(v, 1)

    def from_user(self, value: Any) -> float:
        v = float(value)
        return round(f_to_c(v) if self.fahrenheit else v, 1)

    def svc_value(self, skey: tuple[int, int], ctype: str) -> Any:
        iid = self.services.get(skey, {}).get(ctype)
        return None if iid is None else self.values.get((skey[0], iid))

    def svc_char(self, skey: tuple[int, int], ctype: str) -> dict[str, Any] | None:
        iid = self.services.get(skey, {}).get(ctype)
        return None if iid is None else self.chars.get((skey[0], iid))

    def has(self, skey: tuple[int, int], ctype: str) -> bool:
        return ctype in self.services.get(skey, {})

    def locate(self, entity: EntityEntry) -> tuple[tuple[int, int], int | None] | None:
        parsed = parse_unique_id(entity.unique_id)
        if parsed is None:
            return None
        _pid, aid, sid, iid = parsed
        return (aid, sid), iid

    # -- publishing ----------------------------------------------------------
    def publish_all(self) -> None:
        if not self.available:
            return
        for ent in self.entities:
            try:
                self.publish_entity(ent)
            except Exception:
                self.log.exception("publishing %s failed", ent.entity_id)

    def publish_entity(self, ent: EntityEntry) -> None:
        loc = self.locate(ent)
        if loc is None:
            return
        skey, iid = loc
        if ent.domain == "climate" and iid is None:
            self.publish_climate(ent, skey)
            return
        if iid is None:
            if ent.domain in hks.SERVICE_ENTITY_DOMAINS:
                result = hks.state_of(self, ent, skey)
                if result is None:
                    self.mark_unavailable([ent])
                else:
                    self.publish(ent, *result)
            return
        meta = self.chars.get((skey[0], iid))
        if meta is None:
            self.mark_unavailable([ent])
            return
        value = self.values.get((skey[0], iid))
        t = meta["type"]
        attrs: dict[str, Any] = {}
        spec = self.spec_for(ent)
        caps = spec.get("capabilities") or {}
        if caps.get("state_class"):
            attrs["state_class"] = caps["state_class"]
        if spec.get("original_device_class"):
            attrs["device_class"] = spec["original_device_class"]
        if ent.domain == "sensor":
            if t in (CH_CURRENT_TEMP, CH_TARGET_TEMP, CH_HEAT_THRESHOLD, CH_COOL_THRESHOLD):
                unit = self.temp_unit
                attrs["unit_of_measurement"] = unit
                if value is not None:
                    value = round(c_to_f(float(value)), 1) if unit == "°F" else round(float(value), 1)
            elif t in (CH_CURRENT_HUMIDITY, CH_TARGET_HUMIDITY):
                attrs["unit_of_measurement"] = "%"
            self.publish(ent, UNKNOWN if value is None else value, attrs)
        elif ent.domain == "select":
            if t == CH_DISPLAY_UNITS:
                options, state = ["celsius", "fahrenheit"], DISPLAY_UNITS.get(value)
            elif t == ECOBEE_CURRENT_MODE:
                options, state = list(ECOBEE_MODES.values()), ECOBEE_MODES.get(value)
            else:
                options, state = list(meta.get("valid") or caps.get("options") or []), value
            attrs["options"] = options
            self.publish(ent, state if state is not None else UNKNOWN, attrs)
        elif ent.domain == "button":
            cur = self.hub.states.get(ent.entity_id)
            keep = cur.state if cur is not None and cur.state not in ("unavailable",) else UNKNOWN
            self.publish(ent, keep, attrs)
        elif ent.domain == "binary_sensor":
            self.publish(ent, UNKNOWN if value is None else ("on" if value else "off"), attrs)
        else:
            self.publish(ent, UNKNOWN if value is None else value, attrs)

    def climate_features(self, skey: tuple[int, int]) -> int:
        f = TURN_OFF | TURN_ON
        if self.has(skey, CH_TARGET_TEMP):
            f |= TARGET_TEMPERATURE
        if self.has(skey, CH_HEAT_THRESHOLD) and self.has(skey, CH_COOL_THRESHOLD):
            f |= TARGET_TEMPERATURE_RANGE
        if self.has(skey, CH_TARGET_HUMIDITY):
            f |= TARGET_HUMIDITY
        if self.has(skey, CH_FAN_TARGET):
            f |= FAN_MODE
        return f

    def hvac_modes(self, skey: tuple[int, int]) -> list[str]:
        meta = self.svc_char(skey, CH_TARGET_STATE) or {}
        valid = meta.get("valid")
        if not valid:
            lo, hi = meta.get("min", 0) or 0, meta.get("max", 3)
            valid = list(range(int(lo), int(hi if hi is not None else 3) + 1))
        return [MODE_TO_HA[v] for v in valid if v in MODE_TO_HA]

    def climate_attributes(self, skey: tuple[int, int]) -> dict[str, Any]:
        features = self.climate_features(skey)
        mode = MODE_TO_HA.get(self.svc_value(skey, CH_TARGET_STATE))
        ranged = bool(features & TARGET_TEMPERATURE_RANGE) and mode == "heat_cool"
        if ranged:
            lo_meta = self.svc_char(skey, CH_HEAT_THRESHOLD) or {}
            hi_meta = self.svc_char(skey, CH_COOL_THRESHOLD) or {}
        else:
            lo_meta = hi_meta = self.svc_char(skey, CH_TARGET_TEMP) or {}
        min_c = lo_meta.get("min") if lo_meta.get("min") is not None else 7
        max_c = hi_meta.get("max") if hi_meta.get("max") is not None else 35
        attrs: dict[str, Any] = {
            "hvac_modes": self.hvac_modes(skey),
            "min_temp": self.to_user(min_c, whole=True),
            "max_temp": self.to_user(max_c, whole=True),
        }
        if features & FAN_MODE:
            attrs["fan_modes"] = ["on", "auto"]
        if features & TARGET_HUMIDITY:
            hmeta = self.svc_char(skey, CH_TARGET_HUMIDITY) or {}
            attrs["min_humidity"] = int(hmeta["min"]) if hmeta.get("min") is not None else 30
            attrs["max_humidity"] = int(hmeta["max"]) if hmeta.get("max") is not None else 99
        attrs["current_temperature"] = self.to_user(self.svc_value(skey, CH_CURRENT_TEMP), whole=True)
        # HA's homekit climate: one target in heat/cool (or when the device has no
        # range), the two thresholds in heat_cool, and no target at all when off.
        if mode in ("heat", "cool") or not features & TARGET_TEMPERATURE_RANGE:
            attrs["temperature"] = self.to_user(self.svc_value(skey, CH_TARGET_TEMP), whole=True)
        else:
            attrs["temperature"] = None
        if ranged:
            attrs["target_temp_high"] = self.to_user(self.svc_value(skey, CH_COOL_THRESHOLD), whole=True)
            attrs["target_temp_low"] = self.to_user(self.svc_value(skey, CH_HEAT_THRESHOLD), whole=True)
        elif features & TARGET_TEMPERATURE_RANGE:
            attrs["target_temp_high"] = None
            attrs["target_temp_low"] = None
        humidity = self.svc_value(skey, CH_CURRENT_HUMIDITY)
        if humidity is not None:
            attrs["current_humidity"] = float(humidity)
        if features & TARGET_HUMIDITY:
            target_h = self.svc_value(skey, CH_TARGET_HUMIDITY)
            attrs["humidity"] = None if target_h is None else float(target_h)
        if features & FAN_MODE:
            fan_target = self.svc_value(skey, CH_FAN_TARGET)
            attrs["fan_mode"] = None if fan_target is None else ("auto" if fan_target else "on")
        attrs["hvac_action"] = self.hvac_action(skey)
        attrs["temperature_unit"] = self.temp_unit
        return attrs

    def hvac_action(self, skey: tuple[int, int]) -> str | None:
        # As HA: a thermostat switched off is idle whatever it reports (some
        # devices claim to heat while off), and an ecobee that is idle but
        # moving air shows "fan".
        if self.svc_value(skey, CH_TARGET_STATE) == 0:
            return "idle"
        action = ACTION_TO_HA.get(self.svc_value(skey, CH_CURRENT_STATE))
        if action == "idle" and self.svc_value(skey, CH_FAN_CURRENT) == 2:
            return "fan"
        return action

    def publish_climate(self, ent: EntityEntry, skey: tuple[int, int]) -> None:
        if skey not in self.services:
            self.mark_unavailable([ent])
            return
        target = self.svc_value(skey, CH_TARGET_STATE)
        if target in MODE_TO_HA and target != 0:
            self._last_on_mode[skey] = target
        attrs = self.climate_attributes(skey)
        attrs["supported_features"] = self.climate_features(skey)
        self.publish(ent, MODE_TO_HA.get(target, UNKNOWN), attrs)

    # -- services ------------------------------------------------------------
    async def _put(self, writes: list[tuple[int, int, Any]]) -> None:
        if self._pairing is None:
            raise DeviceUnavailable(f"{self.entry.title} is not connected")
        try:
            result = await asyncio.wait_for(self._pairing.put_characteristics(writes), REQUEST_TIMEOUT)
        except Exception as exc:
            raise DeviceUnavailable(f"{self.entry.title} did not accept the change: {exc}") from exc
        failed = {k: v for k, v in (result or {}).items()
                  if isinstance(v, dict) and v.get("status") not in (None, 0)}
        if failed:
            raise HubError(f"{self.entry.title} refused: {failed}")
        for aid, iid, value in writes:
            if "pr" in self.chars.get((aid, iid), {}).get("perms", []):
                self.values[(aid, iid)] = value

    async def _read_back(self, keys: list[tuple[int, int]]) -> None:
        readable = [k for k in keys if "pr" in self.chars.get(k, {}).get("perms", [])]
        if not readable or self._pairing is None:
            return
        try:
            result = await asyncio.wait_for(self._pairing.get_characteristics(readable), REQUEST_TIMEOUT)
        except Exception as exc:  # the write went through; the event or next poll will catch up
            self.log.debug("read-back failed: %s", exc)
            return
        for key, item in result.items():
            if isinstance(item, dict) and "value" in item:
                self.values[tuple(key)] = item["value"]  # type: ignore[index]

    def _char_key(self, skey: tuple[int, int], ctype: str) -> tuple[int, int]:
        iid = self.services.get(skey, {}).get(ctype)
        if iid is None:
            raise ServiceNotFound(f"this thermostat has no {ctype} characteristic")
        return (skey[0], iid)

    def _clamp(self, skey: tuple[int, int], ctype: str, celsius: float) -> float:
        meta = self.svc_char(skey, ctype) or {}
        if meta.get("min") is not None:
            celsius = max(celsius, float(meta["min"]))
        if meta.get("max") is not None:
            celsius = min(celsius, float(meta["max"]))
        # Accessories refuse a value off their step (HAP -70410, "invalid value"):
        # 75 F is 23.89 C, which a thermostat stepping by 0.5 C rejects. HA's
        # aiohomekit rounds to minStep the same way before writing.
        step = meta.get("step")
        if step:
            celsius = round(celsius / float(step)) * float(step)
        return round(celsius, 2)

    async def async_call(self, entity: EntityEntry, domain: str, service: str, data: dict[str, Any]) -> Any:
        loc = self.locate(entity)
        if loc is None:
            raise ServiceNotFound(f"cannot map {entity.entity_id} to a HomeKit characteristic")
        skey, iid = loc
        if entity.domain == "climate" and iid is None:
            writes = self.climate_writes(skey, service, data)
        elif entity.domain == "select" and service == "select_option" and iid is not None:
            writes = self.select_writes(skey, iid, str(data.get("option", "")))
        elif entity.domain == "button" and service == "press" and iid is not None:
            writes = [(skey[0], iid, True)]
        elif iid is None and entity.domain in hks.SERVICE_ENTITY_DOMAINS:
            try:
                writes = hks.writes_for(self, entity, skey, service, data)
            except hks.WriteError as exc:
                raise ServiceNotFound(str(exc)) from exc
        else:
            raise ServiceNotFound(f"homekit_controller cannot run {domain}.{service} on {entity.entity_id}")
        await self._put(writes)
        await self._read_back([(a, i) for a, i, _ in writes] + [
            (skey[0], i) for t, i in self.services.get(skey, {}).items() if t != "_service"])
        if entity.domain == "button":
            self.publish(entity, datetime.now(UTC).isoformat(timespec="microseconds"), {})
        self.available = True
        self.publish_all()
        return None

    def select_writes(self, skey: tuple[int, int], iid: int, option: str) -> list[tuple[int, int, Any]]:
        meta = self.chars.get((skey[0], iid)) or {}
        if meta.get("type") == CH_DISPLAY_UNITS:
            table = {v: k for k, v in DISPLAY_UNITS.items()}
            if option not in table:
                raise HubError(f"option must be one of {sorted(table)}")
            return [(skey[0], iid, table[option])]
        if meta.get("type") == ECOBEE_CURRENT_MODE:
            # The ecobee reports its mode on one characteristic and takes a new
            # one on another (HA's EcobeeModeSelect does the same).
            table = {v: k for k, v in ECOBEE_MODES.items()}
            if option not in table:
                raise HubError(f"option must be one of {list(table)}")
            return [(*self._char_key(skey, ECOBEE_SET_HOLD_SCHEDULE), table[option])]
        raise ServiceNotFound("this select cannot be set")

    def climate_writes(self, skey: tuple[int, int], service: str, data: dict[str, Any]) -> list[tuple[int, int, Any]]:
        writes: dict[tuple[int, int], Any] = {}
        current = self.svc_value(skey, CH_TARGET_STATE)
        modes = self.hvac_modes(skey)

        def set_mode(mode: str) -> None:
            if mode not in MODE_FROM_HA or mode not in modes:
                raise HubError(f"hvac_mode must be one of {modes}")
            writes[self._char_key(skey, CH_TARGET_STATE)] = MODE_FROM_HA[mode]

        if service == "set_hvac_mode":
            set_mode(str(data.get("hvac_mode", "")))
        elif service == "turn_off":
            set_mode("off")
        elif service == "turn_on":
            # Back to the mode it was in before it was turned off; HA's default
            # (first of heat_cool, heat, cool) when that is not known.
            last = self._last_on_mode.get(skey)
            if last is not None and MODE_TO_HA.get(last) in modes:
                set_mode(MODE_TO_HA[last])
            else:
                set_mode(next(m for m in ("heat_cool", "heat", "cool") if m in modes))
        elif service == "set_fan_mode":
            fan = str(data.get("fan_mode", ""))
            if fan not in ("on", "auto"):
                raise HubError("fan_mode must be 'on' or 'auto'")
            writes[self._char_key(skey, CH_FAN_TARGET)] = 1 if fan == "auto" else 0
        elif service == "set_humidity":
            writes[self._char_key(skey, CH_TARGET_HUMIDITY)] = float(data["humidity"])
        elif service == "set_temperature":
            mode = MODE_TO_HA.get(current, "off")
            if data.get("hvac_mode") and data["hvac_mode"] != mode:
                mode = str(data["hvac_mode"])
                set_mode(mode)
            temp = data.get("temperature")
            low, high = data.get("target_temp_low"), data.get("target_temp_high")
            ranged = mode == "heat_cool" and self.climate_features(skey) & TARGET_TEMPERATURE_RANGE
            if ranged and low is not None and high is not None:
                lo_c = self._clamp(skey, CH_HEAT_THRESHOLD, self.from_user(low))
                hi_c = self._clamp(skey, CH_COOL_THRESHOLD, self.from_user(high))
                writes[self._char_key(skey, CH_HEAT_THRESHOLD)] = lo_c
                writes[self._char_key(skey, CH_COOL_THRESHOLD)] = hi_c
                mid = self.from_user(temp) if temp is not None else (lo_c + hi_c) / 2
                writes[self._char_key(skey, CH_TARGET_TEMP)] = self._clamp(skey, CH_TARGET_TEMP, mid)
            elif temp is not None:
                writes[self._char_key(skey, CH_TARGET_TEMP)] = self._clamp(skey, CH_TARGET_TEMP, self.from_user(temp))
            elif low is not None or high is not None:
                raise HubError("target_temp_low/high need heat_cool mode on a thermostat that supports a range")
            elif not writes:
                raise HubError("set_temperature needs temperature or target_temp_low/target_temp_high")
        else:
            raise ServiceNotFound(f"homekit_controller cannot run climate.{service}")
        return [(aid, iid, v) for (aid, iid), v in writes.items()]
