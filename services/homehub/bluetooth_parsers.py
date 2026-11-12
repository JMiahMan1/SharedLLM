"""BLE sensor parsers: the Bluetooth-Devices libraries Home Assistant uses, behind one table.

Each library reads a passive advertisement and says what the device is
(maker, model, firmware), whether it is encrypted, and what it measures
(``sensor_state_data``'s ``SensorUpdate``). The hub never decides any of that
itself: a sensor's entities, device class and unit are whatever its parser
reported.

| Domain (HA's) | Library | Bindkey | Typical devices |
|---|---|---|---|
| bthome | bthome-ble | yes (encrypted BTHome) | Shelly BLU, ATC/pvvx-flashed thermometers, DIY BTHome |
| xiaomi_ble | xiaomi-ble | yes (MiBeacon v4/v5) | Mi thermometers, Mi Flora, Qingping-made Mi devices, door/motion |
| qingping | qingping-ble | no | Qingping air monitors and thermometers |
| sensorpush | sensorpush-ble | no | SensorPush HT1/HT.w/HTP.xw |
| thermopro | thermopro-ble | no | ThermoPro TP35x/TP39x |
| inkbird | inkbird-ble | no | Inkbird IBS-TH, iBBQ |
| ruuvitag_ble | ruuvitag-ble | no | RuuviTag |
| govee_ble | govee-ble | no | Govee H50xx/H51xx/H507x thermometers |

Unique ids follow HA's Bluetooth passive processors: ``{ADDRESS}-{key}`` and
``{ADDRESS}-{key}-{device_id}`` for a multi-device sensor; the entry's
unique_id is the address.

Libraries are imported lazily: one that fails to import costs only its own
devices, and ``status()`` says which and why.
"""
from __future__ import annotations

import importlib
import logging
import time
from dataclasses import dataclass
from importlib import metadata
from typing import Any

log = logging.getLogger("homehub.bluetooth.parsers")

#: domain -> (label, "module:Class", takes a bindkey, PyPI name). Tried in this order: the loosest last.
PARSERS: dict[str, tuple[str, str, bool, str]] = {
    "bthome": ("BTHome", "bthome_ble:BTHomeBluetoothDeviceData", True, "bthome-ble"),
    "xiaomi_ble": ("Xiaomi BLE", "xiaomi_ble:XiaomiBluetoothDeviceData", True, "xiaomi-ble"),
    "qingping": ("Qingping", "qingping_ble:QingpingBluetoothDeviceData", False, "qingping-ble"),
    "sensorpush": ("SensorPush", "sensorpush_ble:SensorPushBluetoothDeviceData", False, "sensorpush-ble"),
    "thermopro": ("ThermoPro", "thermopro_ble:ThermoProBluetoothDeviceData", False, "thermopro-ble"),
    "inkbird": ("INKBIRD", "inkbird_ble:INKBIRDBluetoothDeviceData", False, "inkbird-ble"),
    "ruuvitag_ble": ("Ruuvi", "ruuvitag_ble:RuuvitagBluetoothDeviceData", False, "ruuvitag-ble"),
    "govee_ble": ("Govee BLE", "govee_ble:GoveeBluetoothDeviceData", False, "govee-ble"),
}

BINDKEY = {"key": "bindkey", "label": "Bindkey", "type": "password",
           "help": "The device's encryption key, 32 hex characters (24 for the oldest Xiaomi devices)."}

#: HA sensor device classes; a parser's class outside this set is kept as a key, not a device class.
HA_SENSOR_CLASSES = {
    "apparent_power", "aqi", "atmospheric_pressure", "battery", "carbon_dioxide", "carbon_monoxide", "conductivity",
    "current", "distance", "duration", "energy", "frequency", "gas", "humidity", "illuminance", "irradiance",
    "moisture", "nitrogen_dioxide", "ozone", "pm1", "pm10", "pm25", "power", "power_factor", "precipitation",
    "precipitation_intensity", "pressure", "reactive_power", "signal_strength", "sound_pressure", "speed",
    "sulphur_dioxide", "temperature", "timestamp", "volatile_organic_compounds",
    "volatile_organic_compounds_parts", "voltage", "volume", "volume_storage", "water", "weight", "wind_speed",
}
HA_BINARY_CLASSES = {
    "battery", "battery_charging", "carbon_monoxide", "cold", "connectivity", "door", "garage_door", "gas", "heat",
    "light", "lock", "moisture", "motion", "moving", "occupancy", "opening", "plug", "power", "presence", "problem",
    "running", "safety", "smoke", "sound", "tamper", "update", "vibration", "window",
}
#: Readings HA keeps off by default: they change with every advertisement.
DISABLED_BY_DEFAULT = {"signal_strength", "packet_id", "rssi"}

_classes: dict[str, Any] = {}
_errors: dict[str, str] = {}


def parser_class(domain: str) -> Any | None:
    if domain in _classes:
        return _classes[domain]
    if domain in _errors or domain not in PARSERS:
        return None
    module_name, _, cls_name = PARSERS[domain][1].partition(":")
    try:
        cls = getattr(importlib.import_module(module_name), cls_name)
    except Exception as exc:  # one broken library costs only its own devices, and says so
        _errors[domain] = f"{type(exc).__name__}: {exc}"
        log.error("BLE parser %s unavailable: %s", domain, exc)
        return None
    _classes[domain] = cls
    return cls


def new_parser(domain: str, bindkey: bytes | None = None) -> Any:
    cls = parser_class(domain)
    if cls is None:
        raise RuntimeError(f"the {domain} parser is not available: {_errors.get(domain, 'unknown domain')}")
    return cls(bindkey) if PARSERS[domain][2] else cls()


def status() -> list[dict[str, Any]]:
    out = []
    for domain, (label, _target, bindkey, dist) in PARSERS.items():
        try:
            version = metadata.version(dist)
        except metadata.PackageNotFoundError:
            version = None
        parser_class(domain)
        out.append({"domain": domain, "label": label, "library": dist, "version": version, "bindkey": bindkey,
                    "error": _errors.get(domain) if version is not None else f"{dist} is not installed"})
    return out


def bindkey_bytes(value: Any) -> bytes:
    """A bindkey typed by a person -> bytes; ValueError saying what is wrong."""
    text = str(value or "").strip().replace(" ", "").replace(":", "")
    if len(text) not in (24, 32):
        raise ValueError("A bindkey is 32 hex characters (24 for the oldest Xiaomi devices).")
    try:
        return bytes.fromhex(text)
    except ValueError:
        raise ValueError("A bindkey has only the characters 0-9 and a-f.") from None


def service_info(adv: Any) -> Any:
    """An advertisement as the parsers take it (habluetooth's ``BluetoothServiceInfoBleak``)."""
    from bleak.backends.device import BLEDevice
    from habluetooth import BluetoothServiceInfoBleak

    return BluetoothServiceInfoBleak(
        adv.name or "", adv.address, int(adv.rssi), dict(adv.manufacturer_data), dict(adv.service_data),
        list(adv.service_uuids), adv.scanner_id, BLEDevice(adv.address, adv.name or "", None), None,
        bool(adv.connectable), time.monotonic(), adv.tx_power)


@dataclass
class Match:
    domain: str
    label: str
    title: str
    manufacturer: str | None
    model: str | None
    encrypted: bool

    def public(self) -> dict[str, Any]:
        return dict(self.__dict__)


def is_encrypted(parser: Any) -> bool:
    """Whether the parser saw an encrypted payload (bthome and xiaomi say so through ``encryption_scheme``)."""
    scheme = getattr(parser, "encryption_scheme", None)
    return scheme is not None and str(getattr(scheme, "value", scheme)).lower() not in ("none", "")


def identify(adv: Any) -> Match | None:
    """Which parser understands this advertisement, and what it says the device is."""
    if not adv.service_data and not adv.manufacturer_data:
        return None
    si = service_info(adv)
    for domain, (label, _t, _bk, _d) in PARSERS.items():
        if parser_class(domain) is None:
            continue
        parser = new_parser(domain)
        try:
            if not parser.supported(si):
                continue
        except Exception as exc:  # a parser choking on a stranger's advertisement is not this device's fault
            log.debug("%s parser failed on %s: %s", domain, adv.address, exc)
            continue
        encrypted = is_encrypted(parser)
        info = (getattr(parser, "_device_id_info", None) or {}).get(None)
        title = parser.title or getattr(info, "name", None) or adv.name or adv.address
        return Match(domain, label, str(title), getattr(info, "manufacturer", None), getattr(info, "model", None),
                     encrypted)
    return None


@dataclass
class Reading:
    """One value a parser reported, in the hub's terms."""

    domain: str            # sensor | binary_sensor
    unique_id: str
    key: str
    name: str
    value: Any
    device_class: str | None
    unit: str | None
    device_id: str | None


def _enum(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def readings(address: str, update: Any) -> list[Reading]:
    """A parser's ``SensorUpdate`` as the hub's entities would carry it."""
    out: list[Reading] = []
    for dkey, desc in (update.entity_descriptions or {}).items():
        value = (update.entity_values or {}).get(dkey)
        if value is None:
            continue
        uid = f"{address}-{dkey.key}" + (f"-{dkey.device_id}" if dkey.device_id else "")
        cls = _enum(desc.device_class)
        out.append(Reading("sensor", uid, dkey.key, value.name or dkey.key.replace("_", " ").capitalize(),
                           value.native_value, cls if cls in HA_SENSOR_CLASSES else None,
                           _enum(desc.native_unit_of_measurement), dkey.device_id))
    for dkey, desc in (update.binary_entity_descriptions or {}).items():
        value = (update.binary_entity_values or {}).get(dkey)
        if value is None:
            continue
        uid = f"{address}-{dkey.key}" + (f"-{dkey.device_id}" if dkey.device_id else "")
        cls = _enum(desc.device_class)
        out.append(Reading("binary_sensor", uid, dkey.key, value.name or dkey.key.replace("_", " ").capitalize(),
                           value.native_value, cls if cls in HA_BINARY_CLASSES else None, None, dkey.device_id))
    return out


def device_fields(update: Any, device_id: str | None = None) -> dict[str, Any]:
    """What the parser says the device is (``DEVICE_INFO_FIELDS``); unset when it does not say."""
    info = (update.devices or {}).get(device_id)
    if info is None:
        return {}
    return {k: v for k, v in {"manufacturer": info.manufacturer, "model": info.model, "sw_version": info.sw_version,
                              "hw_version": info.hw_version}.items() if v}


def entity_fields(r: Reading) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if r.device_class:
        fields["original_device_class"] = r.device_class
    if r.unit:
        fields["unit_of_measurement"] = r.unit
    if r.domain == "sensor" and isinstance(r.value, (int, float)) and not isinstance(r.value, bool):
        fields["capabilities"] = {"state_class": "measurement"}
    if r.key in DISABLED_BY_DEFAULT:
        fields["entity_category"] = "diagnostic"
    return fields


def state_of(r: Reading) -> Any:
    if r.domain == "binary_sensor":
        return "on" if r.value else "off"
    return r.value
