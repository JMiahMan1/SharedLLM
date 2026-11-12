"""Bluetooth devices the hub hears: BLE sensors (one entry per sensor) and iBeacon trackers.

**Sensors** (domains in ``bluetooth_parsers.PARSERS``; entry data ``address``
and, for an encrypted sensor, ``bindkey``): the driver listens on the
Bluetooth bus (``services/homehub/bluetooth.py``) for its address, runs the
parser library on each advertisement and publishes every reading. A reading
the parser reports for the first time (a sensor that sends some values only
now and then) is registered then, with the parser's name, device class and
unit, confirmed as the device's own. The device's maker, model and firmware
are confirmed from the parser on the first reading. A sensor not heard for
``SILENT_S`` turns ``unavailable`` (a sleepy device's last reading is kept
until then).

**iBeacon trackers** (domain ``ibeacon``, entry data ``uuid``, ``major``,
``minor``, ``tx_power``): the driver registers the beacon with the Bluetooth
manager, which publishes the entry's room and distance sensors.
"""
from __future__ import annotations

from typing import Any

from services.homehub import bluetooth_parsers as parsers
from services.homehub import bluetooth_presence as bp
from services.homehub.core import UNAVAILABLE, DeviceUnavailable, Driver, EntityEntry

#: A sensor not heard for this long is unavailable (HA's default for passive Bluetooth devices).
SILENT_S = 900.0


def _manager(hub: Any) -> Any:
    mgr = getattr(hub, "bluetooth", None)
    if mgr is None:
        raise DeviceUnavailable("the hub's Bluetooth platform is not running")
    return mgr


class BLESensorDriver(Driver):
    poll_interval = 60.0

    def __init__(self, hub: Any, entry: Any) -> None:
        super().__init__(hub, entry)
        self.platform = entry.domain
        self.address = str(entry.data.get("address") or entry.unique_id or "").upper()
        self._unsub: Any = None
        self._parser: Any = None
        self._last_heard: float | None = None
        self._confirmed_device = False
        self._decrypt_error_logged = False

    async def async_setup(self) -> None:
        if not self.address:
            raise DeviceUnavailable("this Bluetooth sensor entry has no address")
        mgr = _manager(self.hub)
        key = self.entry.data.get("bindkey")
        try:
            self._parser = parsers.new_parser(self.entry.domain, bytes.fromhex(key) if key else None)
        except RuntimeError as exc:
            raise DeviceUnavailable(str(exc)) from exc
        self._unsub = mgr.listen(self.address, self._on_adv)
        last = mgr.last_advertisement(self.address)
        if last is not None:
            self._on_adv(last)

    async def async_unload(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def async_poll(self) -> None:
        mgr = getattr(self.hub, "bluetooth", None)
        if mgr is None or self._last_heard is None or mgr.clock() - self._last_heard > SILENT_S:
            stale = [e for e in self.entities if (s := self.hub.states.get(e.entity_id)) is None
                     or s.state != UNAVAILABLE]
            if stale and self._last_heard is not None:
                self.mark_unavailable(stale)

    def _device_id(self) -> str | None:
        return next((e.device_id for e in self.entities if e.device_id), None) or next(
            (d.id for d in self.hub.devices.values() if self.entry.entry_id in d.config_entries), None)

    def _on_adv(self, adv: Any) -> None:
        update = self._parser.update(parsers.service_info(adv))
        if parsers.is_encrypted(self._parser) and not getattr(self._parser, "bindkey_verified", False):
            if not self._decrypt_error_logged:
                self.log.warning("%s: the stored bindkey does not decrypt this sensor; re-add it with its key",
                                 self.entry.title)
                self._decrypt_error_logged = True
            return
        self._last_heard = adv.time
        dev_id = self._device_id()
        if not self._confirmed_device and dev_id and update.devices:
            self.confirm_device(dev_id, **parsers.device_fields(update))
            self._confirmed_device = True
        by_uid = {e.unique_id: e for e in self.hub.entities.values() if e.config_entry_id == self.entry.entry_id}
        for r in parsers.readings(self.address, update):
            ent = by_uid.get(r.unique_id) or self._register(r, dev_id)
            if ent is None or ent.disabled_by:
                continue
            attrs: dict[str, Any] = {}
            if r.device_class:
                attrs["device_class"] = r.device_class
            if r.unit:
                attrs["unit_of_measurement"] = r.unit
            if r.domain == "sensor" and isinstance(r.value, (int, float)) and not isinstance(r.value, bool):
                attrs["state_class"] = "measurement"
            self.publish(ent, parsers.state_of(r), attrs)

    def _register(self, r: parsers.Reading, dev_id: str | None) -> EntityEntry:
        from services.homehub.flows import free_entity_id, slugify

        dev = self.hub.devices.get(dev_id or "")
        base = slugify(f"{dev.display_name if dev else self.entry.title} {r.name}")
        fields = parsers.entity_fields(r)
        ent = EntityEntry(entity_id=free_entity_id(self.hub, r.domain, base, set()), platform=self.entry.domain,
                          unique_id=r.unique_id, config_entry_id=self.entry.entry_id, device_id=dev_id,
                          original_name=r.name, has_entity_name=True,
                          disabled_by="integration" if r.key in parsers.DISABLED_BY_DEFAULT else None, **fields)
        self.hub.entities[ent.entity_id] = ent
        self.hub.confirm_entity(ent, original_name=r.name, **fields)
        if self.hub.store is not None:
            self.hub.store.save_entity(ent)
        self.log.info("%s reports a new reading %s: %s", self.entry.title, r.key, ent.entity_id)
        return ent


class IBeaconDriver(Driver):
    platform = "ibeacon"
    poll_interval = None

    def __init__(self, hub: Any, entry: Any) -> None:
        super().__init__(hub, entry)
        self.key: str | None = None

    def _entity(self, suffix: str) -> str | None:
        return next((e.entity_id for e in self.entities if e.unique_id.endswith(suffix)), None)

    async def async_setup(self) -> None:
        from services.homehub.bluetooth import Tracker

        mgr = _manager(self.hub)
        try:
            beacon = bp.beacon_from_dict(self.entry.data)
        except ValueError as exc:
            raise DeviceUnavailable(f"this iBeacon entry is not valid: {exc}") from exc
        self.key = beacon.key
        mgr.add_tracker(Tracker(beacon.key, "ibeacon", self.entry.entry_id, self.entry.title, beacon.tx_power,
                                self._entity("_ble_room"), self._entity("_ble_room_distance")))

    async def async_unload(self) -> None:
        mgr = getattr(self.hub, "bluetooth", None)
        if mgr is not None and self.key:
            mgr.remove_tracker(self.key, self.entry.entry_id)

    def entity_abilities(self, entity: EntityEntry) -> list[str]:
        return ["presence_room"] if entity.unique_id.endswith(("_ble_room", "_ble_room_distance")) else []
