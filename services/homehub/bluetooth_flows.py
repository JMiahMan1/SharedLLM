"""Adding Bluetooth devices: BLE sensors found by their parsers, and iBeacon trackers.

| Integration | Input | What is created |
|---|---|---|
| bthome, xiaomi_ble, govee_ble, ... (``bluetooth_parsers.PARSERS``) | ``bindkey`` when the parser says the device is encrypted | entry ``{address, bindkey?}`` (unique_id = address); one entity per reading the parser reported, ``{ADDRESS}-{key}`` |
| ibeacon | a name (and, by hand, the beacon's uuid/major/minor; ``tx_power`` optional) | entry ``{uuid, major, minor, tx_power}`` (unique_id ``{uuid}_{major}_{minor}``); ``sensor.<name>_room`` and ``sensor.<name>_room_distance`` |

A sensor is added from what its parser makes of the advertisement the hub last
heard from it (so the device must be in range), decrypted with the bindkey when
it is encrypted: a key that does not decrypt is asked for again. Every device
and entity field comes from the parser. The bindkey is kept in the entry's
data, which the store seals; no API returns it.
"""
from __future__ import annotations

import logging
from typing import Any

from services.homehub import bluetooth_parsers as parsers
from services.homehub import bluetooth_presence as bp
from services.homehub.core import ConfigEntry
from services.homehub.flows import FlowError, NeedsInput, Plan, _existing_entry, _new_entry_id, commit

log = logging.getLogger("homehub.bluetooth.flows")

BEACON_FIELDS = [
    {"key": "uuid", "label": "Beacon UUID", "type": "text", "help": "The iBeacon UUID the tag advertises."},
    {"key": "major", "label": "Major", "type": "text", "help": "0 to 65535."},
    {"key": "minor", "label": "Minor", "type": "text", "help": "0 to 65535."},
    {"key": "tx_power", "label": "Power at 1 m (dBm)", "type": "text", "optional": True,
     "help": "Empty: what the beacon advertises."},
]
NAME_FIELD = {"key": "name", "label": "Name", "type": "text", "help": "Whose tag, e.g. Keys or a person's name."}


def needs_for(finding: dict[str, Any]) -> list[dict[str, Any]] | None:
    integration = finding.get("integration")
    if integration in parsers.PARSERS:
        return [parsers.BINDKEY] if (finding.get("props") or {}).get("encrypted") else []
    if integration == "ibeacon":
        return [] if finding.get("source") == "bluetooth" else [*BEACON_FIELDS]
    return None


def _manager(runtime: Any) -> Any:
    mgr = getattr(runtime.hub, "bluetooth", None)
    if mgr is None:
        raise FlowError("Bluetooth is not running in the hub, so Bluetooth devices cannot be added.", 503)
    return mgr


async def adopt_sensor(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                       user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    domain = str(finding.get("integration") or "")
    props = finding.get("props") or {}
    address = str(props.get("address") or "").upper()
    if not address:
        raise FlowError("This finding has no Bluetooth address", 400)
    if _existing_entry(hub, domain, address):
        raise FlowError("This Bluetooth sensor is already set up", 409)
    mgr = _manager(runtime)
    adv = mgr.last_advertisement(address)
    if adv is None:
        raise FlowError("The hub has not heard this sensor since it started; bring it in range of a scanner "
                        "and try again.", 409)
    takes_key = parsers.PARSERS[domain][2]
    bindkey: bytes | None = None
    raw_key = str(user_input.get("bindkey") or "").strip()
    if raw_key and takes_key:
        try:
            bindkey = parsers.bindkey_bytes(raw_key)
        except ValueError as exc:
            raise NeedsInput([parsers.BINDKEY], "Enter the sensor's bindkey.", str(exc)) from exc
    try:
        parser = parsers.new_parser(domain, bindkey)
    except RuntimeError as exc:
        raise FlowError(str(exc), 503) from exc
    # One parse only: parsers drop a repeated packet (BTHome packet ids, encryption counters).
    update = parser.update(parsers.service_info(adv))
    if not update.devices and not update.entity_descriptions and not update.binary_entity_descriptions:
        raise FlowError(f"The {parsers.PARSERS[domain][0]} parser no longer understands this device", 502)
    if takes_key and parsers.is_encrypted(parser):
        if bindkey is None:
            raise NeedsInput([parsers.BINDKEY], f"{finding.get('name') or address} is encrypted: enter its bindkey.")
        if not getattr(parser, "bindkey_verified", False):
            raise NeedsInput([parsers.BINDKEY], "Enter the sensor's bindkey.",
                             "That bindkey did not decrypt the sensor's last reading.")
    title = update.title or finding.get("name") or address
    data: dict[str, Any] = {"address": address}
    if bindkey is not None:
        data["bindkey"] = bindkey.hex()
    entry = ConfigEntry(entry_id=_new_entry_id(), domain=domain, title=str(title), data=data, source="bluetooth",
                        unique_id=address)
    plan = Plan(hub, entry)
    plan.set_device(str(title), name_by_user=name, area_id=area_id,
                    connections=[["bluetooth", address]], **parsers.device_fields(update))
    for r in parsers.readings(address, update):
        fields = parsers.entity_fields(r)
        if r.key in parsers.DISABLED_BY_DEFAULT:
            fields["disabled_by"] = "integration"
        plan.add_entity(r.domain, r.unique_id, r.name, **fields)
    return await commit(runtime, plan)


def _beacon_input(user_input: dict[str, Any], finding: dict[str, Any]) -> bp.Beacon:
    props = finding.get("props") or {}
    raw = {k: user_input.get(k) if user_input.get(k) not in (None, "") else props.get(k)
           for k in ("uuid", "major", "minor")}
    tx = user_input.get("tx_power")
    raw["tx_power"] = None if tx in (None, "") else tx
    try:
        return bp.beacon_from_dict(raw)
    except ValueError as exc:
        raise NeedsInput([*BEACON_FIELDS, NAME_FIELD], "Enter the beacon's identity.", str(exc)) from exc


async def adopt_ibeacon(runtime: Any, finding: dict[str, Any], name: str | None, area_id: str | None,
                        user_input: dict[str, Any]) -> dict[str, Any]:
    hub = runtime.hub
    beacon = _beacon_input(user_input, finding)
    label = str(user_input.get("name") or name or "").strip()
    if not label:
        raise NeedsInput([NAME_FIELD], "Name this tag.")
    unique = f"{beacon.uuid}_{beacon.major}_{beacon.minor}"
    if _existing_entry(hub, "ibeacon", unique):
        raise FlowError("This beacon is already tracked", 409)
    mgr = _manager(runtime)
    existing = mgr.trackers.get(beacon.key)
    if existing is not None:
        raise FlowError(f"This beacon is already tracked as {existing.name}", 409)
    entry = ConfigEntry(entry_id=_new_entry_id(), domain="ibeacon", title=label, source="user", unique_id=unique,
                        data={"uuid": beacon.uuid, "major": beacon.major, "minor": beacon.minor,
                              "tx_power": beacon.tx_power})
    plan = Plan(hub, entry)
    plan.set_device(label, name_by_user=None, area_id=area_id, model="iBeacon",
                    identifiers=[["ibeacon", beacon.key]])
    plan.add_entity("sensor", f"{unique}_ble_room", "Room", original_icon="mdi:map-marker-radius")
    plan.add_entity("sensor", f"{unique}_ble_room_distance", "Room distance", original_icon="mdi:map-marker-radius",
                    original_device_class="distance", unit_of_measurement="m",
                    capabilities={"state_class": "measurement"})
    return await commit(runtime, plan)


FLOWS = {**{d: adopt_sensor for d in parsers.PARSERS}, "ibeacon": adopt_ibeacon}
