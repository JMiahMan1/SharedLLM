"""Generic HomeKit service mapping: which HA entity a HAP service becomes, its state, and its writes.

Driven by the HAP service and characteristic *types* only (Apple's published
short UUIDs), never by a model or a vendor, so any IP accessory, bridge or
bridged accessory that uses the standard services gets the same entities HA's
homekit_controller would give it:

| HAP service (short type) | HA domain | Notes |
|---|---|---|
| Lightbulb 43 | light | on/off, brightness, hs colour, colour temperature |
| Switch 49, Outlet 47, Valve D0 | switch | outlets carry ``outlet_in_use`` |
| Fan 40, Fan v2 B7 | fan | speed as a percentage, direction, oscillation |
| Garage door opener 41 | cover (garage) | open/close, obstruction |
| Window covering 8C, Window 8B, Door 81 | cover | position, stop when the accessory can hold |
| Lock mechanism 45 | lock | locked/unlocked/jammed, locking/unlocking while moving |
| Temperature 8A, Humidity 82, Light 84, Battery 96, CO2 97 | sensor | |
| Motion 85, Contact 80, Occupancy 86, Leak 83, Smoke 87, CO 7F | binary_sensor | |
| Security system 7E | alarm_control_panel | arm home/away/night, disarm |
| Stateless programmable switch 89, Doorbell 121 | event | single/double/long press |
| Thermostat 4A | climate | handled by ``homekit.py`` itself |

Unique ids are HA's: ``{AccessoryPairingID}_{aid}_{service iid}`` for an
entity made from a whole service, ``..._{char iid}`` for one made from one
characteristic (the thermostat's sensors, the display-units select, Identify).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

UNKNOWN = "unknown"

# -- service types ---------------------------------------------------------------------
SVC_ACCESSORY_INFO = "3E"
SVC_THERMOSTAT = "4A"
SVC_LIGHTBULB = "43"
SVC_SWITCH = "49"
SVC_OUTLET = "47"
SVC_VALVE = "D0"
SVC_FAN = "40"
SVC_FAN_V2 = "B7"
SVC_GARAGE = "41"
SVC_WINDOW_COVERING = "8C"
SVC_WINDOW = "8B"
SVC_DOOR = "81"
SVC_LOCK = "45"
SVC_SECURITY_SYSTEM = "7E"
SVC_STATELESS_SWITCH = "89"
SVC_DOORBELL = "121"

# -- characteristic types --------------------------------------------------------------
CH_NAME = "23"
CH_MANUFACTURER = "20"
CH_MODEL = "21"
CH_SERIAL = "30"
CH_FIRMWARE = "52"
CH_HARDWARE = "53"
CH_IDENTIFY = "14"
CH_ON = "25"
CH_BRIGHTNESS = "8"
CH_HUE = "13"
CH_SATURATION = "2F"
CH_COLOR_TEMP = "CE"          # mireds
CH_ACTIVE = "B0"
CH_IN_USE = "D2"
CH_OUTLET_IN_USE = "26"
CH_ROTATION_SPEED = "29"
CH_ROTATION_DIRECTION = "28"  # 0 clockwise, 1 counter-clockwise
CH_SWING = "B6"
CH_DOOR_CURRENT = "E"         # 0 open, 1 closed, 2 opening, 3 closing, 4 stopped
CH_DOOR_TARGET = "32"         # 0 open, 1 closed
CH_OBSTRUCTION = "24"
CH_POSITION_CURRENT = "6D"
CH_POSITION_TARGET = "7C"
CH_POSITION_STATE = "72"      # 0 going down, 1 going up, 2 stopped
CH_HOLD_POSITION = "6F"
CH_LOCK_CURRENT = "1D"        # 0 unsecured, 1 secured, 2 jammed, 3 unknown
CH_LOCK_TARGET = "1E"         # 0 unsecured, 1 secured
CH_ALARM_CURRENT = "66"       # 0 stay, 1 away, 2 night, 3 disarmed, 4 triggered
CH_ALARM_TARGET = "67"        # 0 stay, 1 away, 2 night, 3 disarm
CH_SWITCH_EVENT = "73"        # 0 single, 1 double, 2 long
CH_CURRENT_TEMP = "11"
CH_CURRENT_HUMIDITY = "10"
CH_DISPLAY_UNITS = "36"

#: service type -> HA domain for entities made from a whole service.
SERVICE_DOMAINS: dict[str, str] = {
    SVC_LIGHTBULB: "light",
    SVC_SWITCH: "switch", SVC_OUTLET: "switch", SVC_VALVE: "switch",
    SVC_FAN: "fan", SVC_FAN_V2: "fan",
    SVC_GARAGE: "cover", SVC_WINDOW_COVERING: "cover", SVC_WINDOW: "cover", SVC_DOOR: "cover",
    SVC_LOCK: "lock",
    "8A": "sensor", "82": "sensor", "84": "sensor", "96": "sensor", "97": "sensor",
    "85": "binary_sensor", "80": "binary_sensor", "86": "binary_sensor", "83": "binary_sensor",
    "87": "binary_sensor", "7F": "binary_sensor",
    SVC_SECURITY_SYSTEM: "alarm_control_panel",
    SVC_STATELESS_SWITCH: "event", SVC_DOORBELL: "event",
    SVC_THERMOSTAT: "climate",
}

#: sensor service -> (characteristic, device_class, unit; "temp" means the hub's temperature unit)
SENSORS: dict[str, tuple[str, str, str]] = {
    "8A": (CH_CURRENT_TEMP, "temperature", "temp"),
    "82": (CH_CURRENT_HUMIDITY, "humidity", "%"),
    "84": ("6B", "illuminance", "lx"),
    "96": ("68", "battery", "%"),
    "97": ("93", "carbon_dioxide", "ppm"),
}

#: binary sensor service -> (characteristic, device_class); a non-zero value is "on".
BINARY_SENSORS: dict[str, tuple[str, str]] = {
    "85": ("22", "motion"),
    "80": ("6A", "opening"),          # 0 contact detected (closed), 1 not detected (open)
    "86": ("71", "occupancy"),
    "83": ("70", "moisture"),
    "87": ("76", "smoke"),
    "7F": ("69", "carbon_monoxide"),
}

COVER_CLASSES = {SVC_GARAGE: "garage", SVC_WINDOW: "window", SVC_DOOR: "door", SVC_WINDOW_COVERING: None}
DOOR_STATES = {0: "open", 1: "closed", 2: "opening", 3: "closing", 4: "stopped"}
LOCK_STATES = {0: "unlocked", 1: "locked", 2: "jammed", 3: UNKNOWN}
ALARM_STATES = {0: "armed_home", 1: "armed_away", 2: "armed_night", 3: "disarmed", 4: "triggered"}
ALARM_TARGETS = {"alarm_arm_home": 0, "alarm_arm_away": 1, "alarm_arm_night": 2, "alarm_disarm": 3}
EVENT_TYPES = {0: "single_press", 1: "double_press", 2: "long_press"}

# HA feature bits
LIGHT_FEATURES = 0
FAN_SET_SPEED, FAN_OSCILLATE, FAN_DIRECTION, FAN_TURN_OFF, FAN_TURN_ON = 1, 2, 4, 16, 32
COVER_OPEN, COVER_CLOSE, COVER_SET_POSITION, COVER_STOP = 1, 2, 4, 8
ALARM_ARM_HOME, ALARM_ARM_AWAY, ALARM_ARM_NIGHT = 1, 2, 4
LOCK_FEATURES = 0

#: Domains whose entities this module drives (the thermostat stays in homekit.py).
SERVICE_ENTITY_DOMAINS = frozenset(SERVICE_DOMAINS.values()) - {"climate"}


def short_type(uuid: str) -> str:
    head = str(uuid).split("-", 1)[0].upper()
    return head.lstrip("0") or "0"


# -- registry specs (used when pairing) -------------------------------------------------

def _chars(svc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for ch in svc.get("characteristics", []):
        out.setdefault(short_type(ch["type"]), ch)
    return out


def accessory_info(acc: dict[str, Any]) -> dict[str, Any]:
    """Name, manufacturer, model, serial and versions from the AccessoryInformation service."""
    for svc in acc.get("services", []):
        if short_type(svc["type"]) == SVC_ACCESSORY_INFO:
            c = _chars(svc)

            def val(t: str, c: dict[str, dict[str, Any]] = c) -> Any:
                v = (c.get(t) or {}).get("value")
                return str(v).strip() if v not in (None, "") else None

            return {"name": val(CH_NAME), "manufacturer": val(CH_MANUFACTURER), "model": val(CH_MODEL),
                    "serial_number": val(CH_SERIAL), "sw_version": val(CH_FIRMWARE), "hw_version": val(CH_HARDWARE),
                    "identify_iid": (c.get(CH_IDENTIFY) or {}).get("iid"), "service_iid": svc["iid"]}
    return {}


def entity_specs(pairing_id: str, accessories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every entity HA would make for these accessories.

    Each spec: ``aid``, ``domain``, ``unique_id``, ``name`` (the entity's own
    name, None for the device's main entity) and registry fields
    (``original_device_class``, ``entity_category``, ``capabilities``...).
    """
    specs: list[dict[str, Any]] = []
    for acc in accessories:
        aid = int(acc["aid"])
        info = accessory_info(acc)
        acc_name = (info.get("name") or "").strip().lower()
        primary_taken = False
        for svc in acc.get("services", []):
            stype = short_type(svc["type"])
            domain = SERVICE_DOMAINS.get(stype)
            if domain is None:
                continue
            chars = _chars(svc)
            sid = int(svc["iid"])
            own = (chars.get(CH_NAME) or {}).get("value")
            own = str(own).strip() if own else None
            if own and own.lower() == acc_name:
                own = None
            if own is None and primary_taken:
                # A second unnamed service on one accessory still needs its own name.
                own = {"sensor": SENSORS.get(stype, ("", "Sensor", ""))[1].replace("_", " ").capitalize(),
                       "binary_sensor": BINARY_SENSORS.get(stype, ("", "Sensor"))[1].replace("_", " ").capitalize()
                       }.get(domain, domain.replace("_", " ").capitalize())
            primary_taken = primary_taken or own is None
            base: dict[str, Any] = {"aid": aid, "domain": domain, "unique_id": f"{pairing_id}_{aid}_{sid}",
                                    "name": own}
            if domain == "sensor":
                _ch, dclass, unit = SENSORS[stype]
                base["original_device_class"] = dclass
                if unit != "temp":
                    base["unit_of_measurement"] = unit
                base["capabilities"] = {"state_class": "measurement"}
                if stype == "96":
                    base["entity_category"] = "diagnostic"
            elif domain == "binary_sensor":
                base["original_device_class"] = BINARY_SENSORS[stype][1]
            elif domain == "cover":
                if COVER_CLASSES.get(stype):
                    base["original_device_class"] = COVER_CLASSES[stype]
            elif domain == "switch" and stype == SVC_OUTLET:
                base["original_device_class"] = "outlet"
            elif domain == "event":
                base["original_device_class"] = "doorbell" if stype == SVC_DOORBELL else "button"
                valid = (chars.get(CH_SWITCH_EVENT) or {}).get("valid-values")
                base["capabilities"] = {"event_types": event_types(valid, chars.get(CH_SWITCH_EVENT) or {})}
            specs.append(base)
            if stype == SVC_THERMOSTAT:
                specs.extend(_thermostat_extras(pairing_id, aid, sid, chars))
        if info.get("identify_iid") is not None:
            specs.append({"aid": aid, "domain": "button", "name": "Identify", "entity_category": "diagnostic",
                          "unique_id": f"{pairing_id}_{aid}_{info['service_iid']}_{info['identify_iid']}"})
    return specs


def _thermostat_extras(pid: str, aid: int, sid: int, chars: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The characteristic entities HA adds next to a thermostat's climate entity."""
    out: list[dict[str, Any]] = []
    if CH_CURRENT_TEMP in chars:
        out.append({"aid": aid, "domain": "sensor", "name": "Current Temperature",
                    "unique_id": f"{pid}_{aid}_{sid}_{chars[CH_CURRENT_TEMP]['iid']}",
                    "original_device_class": "temperature", "capabilities": {"state_class": "measurement"}})
    if CH_CURRENT_HUMIDITY in chars:
        out.append({"aid": aid, "domain": "sensor", "name": "Current Humidity",
                    "unique_id": f"{pid}_{aid}_{sid}_{chars[CH_CURRENT_HUMIDITY]['iid']}",
                    "original_device_class": "humidity", "unit_of_measurement": "%",
                    "capabilities": {"state_class": "measurement"}})
    if CH_DISPLAY_UNITS in chars and "pw" in (chars[CH_DISPLAY_UNITS].get("perms") or []):
        out.append({"aid": aid, "domain": "select", "name": "Temperature Display Units", "entity_category": "config",
                    "unique_id": f"{pid}_{aid}_{sid}_{chars[CH_DISPLAY_UNITS]['iid']}",
                    "capabilities": {"options": ["celsius", "fahrenheit"]}})
    return out


def event_types(valid: Any, meta: dict[str, Any]) -> list[str]:
    if not valid:
        lo = meta.get("minValue", 0) or 0
        hi = meta.get("maxValue", 2)
        valid = list(range(int(lo), int(2 if hi is None else hi) + 1))
    return [EVENT_TYPES[v] for v in valid if v in EVENT_TYPES]


# -- state ---------------------------------------------------------------------------

def service_type(drv: Any, skey: tuple[int, int]) -> str | None:
    return drv.services.get(skey, {}).get("_service")


def state_of(drv: Any, ent: Any, skey: tuple[int, int]) -> tuple[Any, dict[str, Any]] | None:
    """``(state, attributes)`` for a service entity, or None when the service is gone."""
    stype = service_type(drv, skey)
    if stype is None:
        return None

    def v(t: str) -> Any:
        return drv.svc_value(skey, t)

    def has(t: str) -> bool:
        return drv.has(skey, t)

    domain = ent.domain
    if domain == "light":
        return _light_state(drv, skey, v, has)
    if domain == "switch":
        on = v(CH_ACTIVE) if stype == SVC_VALVE else v(CH_ON)
        attrs: dict[str, Any] = {}
        if stype == SVC_OUTLET and has(CH_OUTLET_IN_USE):
            attrs["outlet_in_use"] = bool(v(CH_OUTLET_IN_USE))
        if stype == SVC_VALVE and has(CH_IN_USE):
            attrs["in_use"] = bool(v(CH_IN_USE))
        return (UNKNOWN if on is None else ("on" if on else "off")), attrs
    if domain == "fan":
        return _fan_state(drv, skey, v, has)
    if domain == "cover":
        return _cover_state(drv, skey, stype, v, has)
    if domain == "lock":
        cur, target = v(CH_LOCK_CURRENT), v(CH_LOCK_TARGET)
        state = LOCK_STATES.get(cur, UNKNOWN)
        if state in ("locked", "unlocked") and target is not None and target != cur:
            state = "locking" if target == 1 else "unlocking"
        return state, {"supported_features": LOCK_FEATURES}
    if domain == "sensor":
        ch, dclass, unit = SENSORS[stype]
        value = v(ch)
        attrs = {"state_class": "measurement", **({"device_class": dclass} if dclass else {})}
        if unit == "temp":
            attrs["unit_of_measurement"] = drv.temp_unit
            if value is not None:
                value = round(float(value) * 9 / 5 + 32, 1) if attrs["unit_of_measurement"] == "°F" \
                    else round(float(value), 1)
        else:
            attrs["unit_of_measurement"] = unit
        return (UNKNOWN if value is None else value), attrs
    if domain == "binary_sensor":
        value = v(BINARY_SENSORS[stype][0])
        return (UNKNOWN if value is None else ("on" if value else "off")), {}
    if domain == "alarm_control_panel":
        return ALARM_STATES.get(v(CH_ALARM_CURRENT), UNKNOWN), {
            "supported_features": ALARM_ARM_HOME | ALARM_ARM_AWAY | ALARM_ARM_NIGHT, "code_format": None,
            "changed_by": None, "code_arm_required": False}
    if domain == "event":
        stamp = drv.event_stamps.get(skey)
        spec_for = getattr(drv, "spec_for", None)
        spec = spec_for(ent) if callable(spec_for) else {}
        attrs = {"event_types": (spec.get("capabilities") or {}).get("event_types") or list(EVENT_TYPES.values()),
                 "event_type": stamp[1] if stamp else None}
        return (stamp[0] if stamp else UNKNOWN), attrs
    return None


def _light_state(drv: Any, skey: tuple[int, int], v: Any, has: Any) -> tuple[Any, dict[str, Any]]:
    on = v(CH_ON)
    modes: list[str] = []
    if has(CH_HUE) or has(CH_SATURATION):
        modes.append("hs")
    if has(CH_COLOR_TEMP):
        modes.append("color_temp")
    if not modes:
        modes = ["brightness"] if has(CH_BRIGHTNESS) else ["onoff"]
    attrs: dict[str, Any] = {"supported_color_modes": modes, "supported_features": LIGHT_FEATURES}
    if has(CH_COLOR_TEMP):
        meta = drv.svc_char(skey, CH_COLOR_TEMP) or {}
        lo_m, hi_m = meta.get("min") or 140, meta.get("max") or 500
        attrs["min_color_temp_kelvin"] = round(1_000_000 / float(hi_m))
        attrs["max_color_temp_kelvin"] = round(1_000_000 / float(lo_m))
    if on:
        # As HA: HomeKit does not say which colour mode is in use, so a light
        # with hue/saturation reports hs.
        attrs["color_mode"] = modes[0]
        if has(CH_BRIGHTNESS) and v(CH_BRIGHTNESS) is not None:
            attrs["brightness"] = round(float(v(CH_BRIGHTNESS)) * 255 / 100)
        if "hs" in modes:
            attrs["hs_color"] = (float(v(CH_HUE) or 0), float(v(CH_SATURATION) or 0))
        if has(CH_COLOR_TEMP) and v(CH_COLOR_TEMP):
            attrs["color_temp_kelvin"] = round(1_000_000 / float(v(CH_COLOR_TEMP)))
    else:
        attrs["color_mode"] = None
    return (UNKNOWN if on is None else ("on" if on else "off")), attrs


def _speed_max(drv: Any, skey: tuple[int, int]) -> float:
    meta = drv.svc_char(skey, CH_ROTATION_SPEED) or {}
    return float(meta.get("max") or 100) or 100.0


def _fan_on(v: Any, has: Any) -> Any:
    return v(CH_ACTIVE) if has(CH_ACTIVE) else v(CH_ON)


def _fan_state(drv: Any, skey: tuple[int, int], v: Any, has: Any) -> tuple[Any, dict[str, Any]]:
    on = _fan_on(v, has)
    features = FAN_TURN_ON | FAN_TURN_OFF
    attrs: dict[str, Any] = {}
    if has(CH_ROTATION_SPEED):
        features |= FAN_SET_SPEED
        speed = v(CH_ROTATION_SPEED)
        attrs["percentage"] = None if speed is None or not on else round(float(speed) / _speed_max(drv, skey) * 100)
        step = (drv.svc_char(skey, CH_ROTATION_SPEED) or {}).get("step") or 1
        attrs["percentage_step"] = round(float(step) / _speed_max(drv, skey) * 100, 2)
    if has(CH_ROTATION_DIRECTION):
        features |= FAN_DIRECTION
        d = v(CH_ROTATION_DIRECTION)
        attrs["direction"] = None if d is None else ("reverse" if d else "forward")
    if has(CH_SWING):
        features |= FAN_OSCILLATE
        attrs["oscillating"] = None if v(CH_SWING) is None else bool(v(CH_SWING))
    attrs["supported_features"] = features
    return (UNKNOWN if on is None else ("on" if on else "off")), attrs


def _cover_state(drv: Any, skey: tuple[int, int], stype: str, v: Any, has: Any) -> tuple[Any, dict[str, Any]]:
    if stype == SVC_GARAGE:
        attrs: dict[str, Any] = {"supported_features": COVER_OPEN | COVER_CLOSE}
        if has(CH_OBSTRUCTION):
            attrs["obstruction-detected"] = bool(v(CH_OBSTRUCTION))
        return DOOR_STATES.get(v(CH_DOOR_CURRENT), UNKNOWN), attrs
    features = COVER_OPEN | COVER_CLOSE | COVER_SET_POSITION
    if has(CH_HOLD_POSITION):
        features |= COVER_STOP
    pos, moving = v(CH_POSITION_CURRENT), v(CH_POSITION_STATE)
    attrs = {"supported_features": features, "current_position": pos}
    if moving == 0:
        state = "closing"
    elif moving == 1:
        state = "opening"
    elif pos is None:
        state = UNKNOWN
    else:
        state = "closed" if int(pos) == 0 else "open"
    return state, attrs


def note_events(drv: Any, event: dict[Any, Any]) -> None:
    """Stamp stateless switches that just fired (their value is only meaningful as an event)."""
    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    for key, item in (event or {}).items():
        if not isinstance(item, dict) or item.get("value") is None:
            continue
        aid_iid = tuple(key)
        meta = drv.chars.get(aid_iid)
        if meta is None or meta.get("type") != CH_SWITCH_EVENT:
            continue
        skey = drv.service_of.get(aid_iid)
        if skey is not None:
            drv.event_stamps[skey] = (now, EVENT_TYPES.get(item["value"], str(item["value"])))


# -- services ------------------------------------------------------------------------

class WriteError(ValueError):
    """A service the entity cannot run, or data it cannot take."""


def writes_for(drv: Any, ent: Any, skey: tuple[int, int], service: str,
               data: dict[str, Any]) -> list[tuple[int, int, Any]]:
    """The characteristic writes a service call becomes."""
    stype = service_type(drv, skey)
    aid = skey[0]
    out: dict[int, Any] = {}

    def iid(t: str) -> int:
        found = drv.services.get(skey, {}).get(t)
        if found is None:
            raise WriteError(f"{ent.entity_id} has no {t} characteristic")
        return int(found)

    def v(t: str) -> Any:
        return drv.svc_value(skey, t)

    def has(t: str) -> bool:
        return drv.has(skey, t)

    domain = ent.domain
    if domain == "light":
        on = bool(v(CH_ON)) if service == "toggle" else None
        if service == "turn_off" or (service == "toggle" and on):
            out[iid(CH_ON)] = False
        elif service in ("turn_on", "toggle"):
            out[iid(CH_ON)] = True
            if "brightness_pct" in data:
                out[iid(CH_BRIGHTNESS)] = _clampc(drv, skey, CH_BRIGHTNESS, float(data["brightness_pct"]))
            elif "brightness" in data:
                out[iid(CH_BRIGHTNESS)] = _clampc(drv, skey, CH_BRIGHTNESS, float(data["brightness"]) * 100 / 255)
            if data.get("hs_color") is not None:
                hue, sat = data["hs_color"]
                out[iid(CH_HUE)] = float(hue)
                out[iid(CH_SATURATION)] = float(sat)
            kelvin = data.get("color_temp_kelvin") or data.get("kelvin")
            if kelvin:
                out[iid(CH_COLOR_TEMP)] = int(_clampc(drv, skey, CH_COLOR_TEMP, 1_000_000 / float(kelvin)))
            elif data.get("color_temp"):
                out[iid(CH_COLOR_TEMP)] = int(_clampc(drv, skey, CH_COLOR_TEMP, float(data["color_temp"])))
        else:
            raise WriteError(f"lights take turn_on, turn_off or toggle, not {service}")
    elif domain == "switch":
        t = CH_ACTIVE if stype == SVC_VALVE else CH_ON
        on = bool(v(t))
        want = {"turn_on": True, "turn_off": False, "toggle": not on}.get(service)
        if want is None:
            raise WriteError(f"switches take turn_on, turn_off or toggle, not {service}")
        out[iid(t)] = (1 if want else 0) if t == CH_ACTIVE else want
    elif domain == "fan":
        power = CH_ACTIVE if has(CH_ACTIVE) else CH_ON

        def set_power(want: bool) -> None:
            out[iid(power)] = (1 if want else 0) if power == CH_ACTIVE else want

        if service == "turn_off" or (service == "toggle" and _fan_on(v, has)):
            set_power(False)
        elif service in ("turn_on", "toggle", "set_percentage"):
            pct = data.get("percentage")
            if pct is not None and float(pct) <= 0:
                set_power(False)
            else:
                set_power(True)
                if pct is not None:
                    out[iid(CH_ROTATION_SPEED)] = _clampc(drv, skey, CH_ROTATION_SPEED,
                                                          float(pct) / 100 * _speed_max(drv, skey))
        elif service == "set_direction":
            direction = str(data.get("direction") or "")
            if direction not in ("forward", "reverse"):
                raise WriteError("direction must be forward or reverse")
            out[iid(CH_ROTATION_DIRECTION)] = 1 if direction == "reverse" else 0
        elif service == "oscillate":
            out[iid(CH_SWING)] = 1 if data.get("oscillating") else 0
        else:
            raise WriteError(f"fans cannot run {service}")
    elif domain == "cover":
        if stype == SVC_GARAGE:
            cur = DOOR_STATES.get(v(CH_DOOR_CURRENT))
            if service == "open_cover" or (service == "toggle" and cur in ("closed", "closing")):
                out[iid(CH_DOOR_TARGET)] = 0
            elif service in ("close_cover", "toggle"):
                out[iid(CH_DOOR_TARGET)] = 1
            else:
                raise WriteError(f"a garage door takes open_cover, close_cover or toggle, not {service}")
        else:
            pos = v(CH_POSITION_CURRENT)
            if service == "open_cover" or (service == "toggle" and (pos or 0) == 0):
                out[iid(CH_POSITION_TARGET)] = 100
            elif service in ("close_cover", "toggle"):
                out[iid(CH_POSITION_TARGET)] = 0
            elif service == "set_cover_position":
                out[iid(CH_POSITION_TARGET)] = max(0, min(100, int(data.get("position", 0))))
            elif service == "stop_cover":
                out[iid(CH_HOLD_POSITION)] = True
            else:
                raise WriteError(f"covers cannot run {service}")
    elif domain == "lock":
        if service not in ("lock", "unlock"):
            raise WriteError(f"locks take lock or unlock, not {service}")
        out[iid(CH_LOCK_TARGET)] = 1 if service == "lock" else 0
    elif domain == "alarm_control_panel":
        if service not in ALARM_TARGETS:
            raise WriteError(f"the security system takes {sorted(ALARM_TARGETS)}, not {service}")
        out[iid(CH_ALARM_TARGET)] = ALARM_TARGETS[service]
    else:
        raise WriteError(f"{ent.entity_id} cannot be controlled")
    return [(aid, i, value) for i, value in out.items()]


def _clampc(drv: Any, skey: tuple[int, int], ctype: str, value: float) -> float:
    meta = drv.svc_char(skey, ctype) or {}
    if meta.get("min") is not None:
        value = max(value, float(meta["min"]))
    if meta.get("max") is not None:
        value = min(value, float(meta["max"]))
    step = meta.get("step")
    if step:
        value = round(value / float(step)) * float(step)
    return round(value) if (meta.get("format") or "").startswith(("int", "uint")) else round(value, 2)
