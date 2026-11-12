"""The hub's Bluetooth platform: one advertisement bus, BLE sensors and room presence.

**Scanners** feed one bus (:meth:`BluetoothManager.ingest`):

- **The host's adapter**, through BlueZ over D-Bus with ``bleak``. It is a
  setting: ``HOMEHUB_BLUETOOTH_ADAPTER`` names it as ``hciN`` or, better, by its
  address (``hciN`` numbers follow USB enumeration order and can swap across
  reboots; the address cannot). Unset, the host adapter is off and
  ``GET /hub/bluetooth`` says so; nothing is guessed. ``HOMEHUB_BLUETOOTH_SCAN_MODE``
  is ``auto`` (passive when BlueZ offers advertisement monitors, i.e.
  bluetoothd runs with ``--experimental``; active otherwise, and the status
  says which and why), ``active`` or ``passive`` (refused, with the reason,
  when BlueZ cannot do it). The container needs the system bus socket and,
  on an SELinux host, ``label=disable`` (README, "Bluetooth").
- **ESPHome Bluetooth proxies**: a node whose ``device_info`` advertises the
  ``bluetooth_proxy`` passive-scan feature streams what it hears over the
  native API connection the ESPHome driver already holds
  (:func:`attach_esphome_proxy`; raw advertisements when the node offers
  them). The node's device area is the scanner's area. Nothing on the node is
  changed.

Each scanner has an id (``bluez_<adapter mac>``, ``esphome_<node mac>``), an
area, a ``ref_power`` (RSSI at 1 m for a beacon that does not say), and an
``rssi_offset`` (dB added to everything it hears, to even out antennas), set
with ``PATCH /hub/bluetooth/scanners/{id}`` and kept in the store
(``bluetooth_scanners``). A proxy's own device area is used unless an area was
set for its scanner.

**BLE sensors** (``bluetooth_parsers.py``): an advertisement a parser library
understands becomes a discovery finding (integration = HA's domain for that
parser, with what the parser says the device is and whether it is encrypted).
``bluetooth_flows.py`` adds it (asking for the bindkey when encrypted) and
``drivers/ble.py`` publishes its readings as they arrive.

**Presence** (``bluetooth_presence.py``): every iBeacon heard is kept per
scanner. A *tracker* is a beacon the household follows: a phone that
registered ``app_data.ble_beacon`` through the mobile_app webhook, or an
``ibeacon`` entry an admin added. Each tracker has a room sensor
(``sensor.<name>_room``) and a distance sensor (``sensor.<name>_room_distance``)
the manager publishes once a second when something changed (at most every
``publish_min_s`` for attribute-only changes). ``person.*`` entities whose
trackers include such a phone carry ``room`` / ``room_area_id``.

**The room map**: an admin records sessions (``POST /hub/bluetooth/map/sessions``)
with a tracker in a room; each 1.5 s window becomes one sample of every online
scanner's RSSI (``None`` for not heard). The sessions are kept in the store
(``bluetooth_map``) and the estimator switches from nearest-scanner to
fingerprints once two rooms are mapped. ``GET /hub/bluetooth/map`` reports the
cross-validated accuracy and ``stale_reason`` when a scanner was added, moved
or went away after mapping.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from services.homehub import bluetooth_parsers as parsers
from services.homehub import bluetooth_presence as bp
from services.homehub.core import UNAVAILABLE, UNKNOWN, Hub, HubError

log = logging.getLogger("homehub.bluetooth")

ADAPTER_ENV = "HOMEHUB_BLUETOOTH_ADAPTER"
MODE_ENV = "HOMEHUB_BLUETOOTH_SCAN_MODE"
KV_SCANNERS = "bluetooth_scanners"
KV_MAP = "bluetooth_map"
TICK_S = 1.0
SAMPLE_S = 1.5
#: A device not heard for this long leaves the "recently heard" list.
DEVICE_EXPIRE_S = 900.0
MAX_DEVICES = 3000
#: A device no parser understood is asked again after this long (its next advertisement may say more).
IDENTIFY_RETRY_S = 60.0
#: A sensor finding's last_seen is refreshed in discovery this often.
DISCOVERY_REFRESH_S = 300.0
#: A tracker must have been heard this recently for a mapping session to start.
HEARD_RECENTLY_S = 30.0
SESSION_MIN_S, SESSION_MAX_S, SESSION_DEFAULT_S = 30, 300, 60
SESSION_KEEP_S = 3600.0
#: The host scanner is restarted when it has heard nothing for this long.
BLUEZ_WATCHDOG_S = 300.0
BLUEZ_RETRY_S = (10, 30, 60, 300)
ROOM_ICON = "mdi:map-marker-radius"
ROOM_KEY, DISTANCE_KEY = "ble_room", "ble_room_distance"


class BluetoothError(HubError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def mac12(address: str) -> str:
    return "".join(c for c in str(address).lower() if c in "0123456789abcdef")


def norm_address(address: str) -> str:
    s = mac12(address).upper()
    return ":".join(s[i:i + 2] for i in range(0, 12, 2)) if len(s) == 12 else str(address).upper()


@dataclass(slots=True)
class Advertisement:
    address: str
    rssi: int
    scanner_id: str
    time: float
    name: str | None = None
    manufacturer_data: dict[int, bytes] = field(default_factory=dict)
    service_data: dict[str, bytes] = field(default_factory=dict)
    service_uuids: list[str] = field(default_factory=list)
    tx_power: int | None = None
    connectable: bool = False


class Scanner:
    """One radio that hears advertisements: the host adapter or a proxy."""

    def __init__(self, scanner_id: str, kind: str, name: str, *, source: str, device_id: str | None = None) -> None:
        self.scanner_id = scanner_id
        self.kind = kind
        self.name = name
        self.source = source
        self.device_id = device_id
        self.connected = False
        self.mode: str | None = None
        self.error: str | None = None
        self.adverts = 0
        self.last_seen: float | None = None
        self._buckets: deque[list[float]] = deque()

    def heard(self, t: float) -> None:
        self.adverts += 1
        self.last_seen = t if self.last_seen is None else max(self.last_seen, t)
        sec = float(int(t))
        if self._buckets and self._buckets[-1][0] == sec:
            self._buckets[-1][1] += 1
        else:
            self._buckets.append([sec, 1])
        while self._buckets and self._buckets[0][0] < sec - 60:
            self._buckets.popleft()

    def rate_per_min(self, now: float) -> int:
        return int(sum(n for s, n in self._buckets if s >= now - 60))


@dataclass
class SeenDevice:
    address: str
    name: str | None = None
    first_seen: float = 0.0
    last_seen: float = 0.0
    rssi: dict[str, tuple[int, float]] = field(default_factory=dict)
    match: parsers.Match | None = None
    identified_at: float | None = None
    beacon: str | None = None
    last: Advertisement | None = None
    found_at: float | None = None


@dataclass
class Tracker:
    key: str
    kind: str                      # "phone" | "ibeacon"
    entry_id: str
    name: str
    tx_power: int | None = None
    room_entity: str | None = None
    distance_entity: str | None = None
    tracker_entity: str | None = None
    state: bp.RoomState = field(default_factory=bp.RoomState)
    last_published: float = float("-inf")
    last_payload: tuple[Any, ...] | None = None


@dataclass
class MapSession:
    session_id: str
    area_id: str
    tracker: str
    key: str
    started: float
    ends: float
    ends_at: str
    status: str = "recording"
    samples: list[dict[str, float | None]] = field(default_factory=list)
    window: dict[str, float] = field(default_factory=dict)
    window_start: float = 0.0
    empty_windows: int = 0
    scanners_heard: set[str] = field(default_factory=set)
    error: str | None = None
    finished: float | None = None


Listener = Callable[[Advertisement], Any]


class BluetoothManager:
    """The bus, the scanners, the devices heard, the trackers and the room map."""

    def __init__(self, hub: Hub, store: Any, *, params: bp.PresenceParams | None = None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 adapter: str | None = None, mode: str | None = None,
                 discovery: Callable[[], Any] | None = None) -> None:
        self.hub = hub
        self.store = store
        self.params = params or bp.PresenceParams()
        self.clock = clock
        self.wall = wall
        self.adapter = (adapter if adapter is not None else os.getenv(ADAPTER_ENV, "")).strip()
        self.mode = (mode if mode is not None else os.getenv(MODE_ENV, "auto")).strip().lower() or "auto"
        self.discovery = discovery or (lambda: None)
        self.scanners: dict[str, Scanner] = {}
        self.settings: dict[str, dict[str, Any]] = dict(store.get_kv(KV_SCANNERS, {}) or {}) if store else {}
        saved_map = (store.get_kv(KV_MAP, {}) or {}) if store else {}
        self.map: dict[str, list[dict[str, Any]]] = {str(k): list(v) for k, v in
                                                     (saved_map.get("areas") or {}).items() if v}
        self.devices: dict[str, SeenDevice] = {}
        self.sightings: dict[str, bp.Sighting] = {}
        self.trackers: dict[str, Tracker] = {}
        self.tracker_errors: dict[str, str] = {}
        self.sessions: dict[str, MapSession] = {}
        self._listeners: dict[str, list[Listener]] = {}
        self._models: dict[str, bp.AreaModel] | None = None
        self._cv: dict[str, Any] | None = None
        self._tasks: list[asyncio.Task] = []
        self._last_prune = 0.0
        self.host_status: dict[str, Any] = {"configured": self.adapter or None, "enabled": bool(self.adapter),
                                            "reason": None if self.adapter else (
                                                f"{ADAPTER_ENV} is not set, so the host's Bluetooth adapter is "
                                                "not used (ESPHome Bluetooth proxies still are)")}
        hub.bluetooth = self  # type: ignore[attr-defined]

    # -- lifecycle -----------------------------------------------------------------------
    async def start(self) -> None:
        if self.adapter:
            if self.mode not in ("auto", "active", "passive"):
                raise BluetoothError(f"{MODE_ENV} must be auto, active or passive, not {self.mode!r}")
            self._tasks.append(asyncio.create_task(BlueZScanner(self, self.adapter, self.mode).run(),
                                                   name="bluetooth:bluez"))
        else:
            log.warning("Bluetooth: %s", self.host_status["reason"])
        self.sync_trackers()
        self._tasks.append(asyncio.create_task(self._ticker(), name="bluetooth:tick"))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks.clear()
        if getattr(self.hub, "bluetooth", None) is self:
            self.hub.bluetooth = None  # type: ignore[attr-defined]

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                self.tick()
            except Exception:
                log.exception("Bluetooth tick failed")

    # -- scanners ------------------------------------------------------------------------
    def add_scanner(self, scanner_id: str, kind: str, name: str, *, source: str,
                    device_id: str | None = None) -> Scanner:
        sc = self.scanners.get(scanner_id)
        if sc is None:
            sc = Scanner(scanner_id, kind, name, source=source, device_id=device_id)
            self.scanners[scanner_id] = sc
        sc.name, sc.source, sc.device_id, sc.kind = name, source, device_id, kind
        sc.connected, sc.error = True, None
        cfg = self.settings.setdefault(scanner_id, {})
        if "first_seen" not in cfg:
            cfg.update({"first_seen": self.wall(), "kind": kind, "name": name})
            self._save_settings()
        return sc

    def scanner_gone(self, scanner_id: str, error: str | None = None) -> None:
        sc = self.scanners.get(scanner_id)
        if sc is not None:
            sc.connected = False
            sc.error = error

    def scanner_area(self, scanner_id: str) -> str | None:
        cfg = self.settings.get(scanner_id) or {}
        if cfg.get("area_id"):
            return str(cfg["area_id"])
        sc = self.scanners.get(scanner_id)
        dev = self.hub.devices.get(sc.device_id or "") if sc is not None else None
        return dev.area_id if dev is not None else None

    def _offsets(self) -> dict[str, float]:
        return {sid: float((self.settings.get(sid) or {}).get("rssi_offset") or 0.0) for sid in self.scanners}

    def online(self, now: float) -> set[str]:
        return {sid for sid, sc in self.scanners.items()
                if sc.connected and sc.last_seen is not None and now - sc.last_seen <= self.params.scanner_online_s}

    def set_scanner(self, scanner_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if scanner_id not in self.scanners and scanner_id not in self.settings:
            raise BluetoothError(f"There is no Bluetooth scanner {scanner_id!r}", 404)
        unknown = sorted(set(patch) - {"area_id", "ref_power", "rssi_offset"})
        if unknown:
            raise BluetoothError(f"Unknown fields: {', '.join(unknown)} (area_id, ref_power, rssi_offset)")
        cfg = dict(self.settings.get(scanner_id) or {})
        if "area_id" in patch:
            area = patch["area_id"]
            if area in (None, ""):
                cfg.pop("area_id", None)
            else:
                found = self.hub.area_id_by_name(str(area))
                if found is None:
                    raise BluetoothError(f"There is no area {area!r}", 400)
                cfg["area_id"] = found
        for key, low, high in (("ref_power", -100, 0), ("rssi_offset", -30, 30)):
            if key not in patch:
                continue
            value = patch[key]
            if value is None:
                cfg.pop(key, None)
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise BluetoothError(f"{key} must be a number from {low} to {high} (dB), not {value!r}")
            cfg[key] = value
        self.settings[scanner_id] = cfg
        self._save_settings()
        return self.scanner_public(scanner_id)

    def _save_settings(self) -> None:
        if self.store is not None:
            self.store.set_kv(KV_SCANNERS, self.settings)

    def scanner_public(self, scanner_id: str) -> dict[str, Any]:
        now = self.clock()
        sc = self.scanners.get(scanner_id)
        cfg = self.settings.get(scanner_id) or {}
        area = self.scanner_area(scanner_id)
        return {
            "id": scanner_id, "kind": (sc.kind if sc else cfg.get("kind")), "name": (sc.name if sc else cfg.get("name")),
            "source": sc.source if sc else None, "device_id": sc.device_id if sc else None,
            "area_id": area, "area": self.hub.areas[area].name if area in self.hub.areas else None,
            "area_source": "setting" if cfg.get("area_id") else ("device" if area else None),
            "ref_power": cfg.get("ref_power"), "rssi_offset": cfg.get("rssi_offset"),
            "connected": bool(sc and sc.connected), "online": scanner_id in self.online(now),
            "mode": sc.mode if sc else None, "advertisements": sc.adverts if sc else 0,
            "rate_per_min": sc.rate_per_min(now) if sc else 0,
            "last_seen": self._iso(sc.last_seen) if sc and sc.last_seen is not None else None,
            "error": sc.error if sc else None,
        }

    # -- the bus ---------------------------------------------------------------------------
    def listen(self, address: str, callback: Listener) -> Callable[[], None]:
        key = norm_address(address)
        self._listeners.setdefault(key, []).append(callback)

        def unsubscribe() -> None:
            items = self._listeners.get(key) or []
            if callback in items:
                items.remove(callback)
            if not items:
                self._listeners.pop(key, None)

        return unsubscribe

    def last_advertisement(self, address: str) -> Advertisement | None:
        dev = self.devices.get(norm_address(address))
        return dev.last if dev is not None else None

    def ingest(self, adv: Advertisement) -> None:
        """One advertisement from any scanner."""
        adv.address = norm_address(adv.address)
        sc = self.scanners.get(adv.scanner_id)
        if sc is not None:
            sc.heard(adv.time)
        dev = self.devices.get(adv.address)
        if dev is None:
            if len(self.devices) >= MAX_DEVICES:
                self._prune(adv.time, force=True)
            dev = SeenDevice(adv.address, first_seen=adv.time)
            self.devices[adv.address] = dev
        dev.last_seen = max(dev.last_seen, adv.time)
        dev.rssi[adv.scanner_id] = (int(adv.rssi), adv.time)
        if adv.name:
            dev.name = adv.name
        dev.last = adv
        beacon = bp.parse_ibeacon(adv.manufacturer_data) if adv.manufacturer_data else None
        if beacon is not None:
            dev.beacon = beacon.key
            self._hear_beacon(beacon, adv)
        for cb in list(self._listeners.get(adv.address) or []):
            try:
                cb(adv)
            except Exception:
                log.exception("Bluetooth listener for %s failed", adv.address)
        if dev.match is None and beacon is None and (
                dev.identified_at is None or adv.time - dev.identified_at >= IDENTIFY_RETRY_S):
            dev.identified_at = adv.time
            try:
                dev.match = parsers.identify(adv)
            except Exception:
                log.exception("identifying %s failed", adv.address)
        if dev.match is not None and (dev.found_at is None or adv.time - dev.found_at >= DISCOVERY_REFRESH_S):
            dev.found_at = adv.time
            self._record_sensor_finding(dev, adv)

    def _hear_beacon(self, beacon: bp.Beacon, adv: Advertisement) -> None:
        sight = self.sightings.get(beacon.key)
        if sight is None:
            sight = self.sightings[beacon.key] = bp.Sighting(beacon.key)
        sight.hear(adv.scanner_id, adv.rssi, adv.time, self.params.ema_alpha, beacon.tx_power)
        for s in self.sessions.values():
            if s.status == "recording" and s.key == beacon.key:
                s.window[adv.scanner_id] = max(s.window.get(adv.scanner_id, -999.0), float(adv.rssi))
                s.scanners_heard.add(adv.scanner_id)
        if beacon.key not in self.trackers and (sight.readings[adv.scanner_id].count == 1):
            self._record_beacon_finding(beacon, adv)

    # -- discovery ---------------------------------------------------------------------------
    def _where(self, adv: Advertisement) -> dict[str, Any]:
        sc = self.scanners.get(adv.scanner_id)
        area = self.scanner_area(adv.scanner_id)
        return {"rssi": adv.rssi, "scanner": sc.name if sc else adv.scanner_id,
                "area": self.hub.areas[area].name if area in self.hub.areas else None}

    def _record(self, finding: dict[str, Any], notify: bool) -> None:
        mgr = self.discovery()
        if mgr is None:
            return
        try:
            known_before = finding["id"] in mgr.findings
            mgr.record(finding)
            if notify and not known_before:
                schedule = getattr(mgr, "_schedule_notices", None)
                if callable(schedule):
                    schedule()
        except Exception:
            log.exception("recording Bluetooth finding %s failed", finding.get("id"))

    def _record_sensor_finding(self, dev: SeenDevice, adv: Advertisement) -> None:
        from services.homehub.discovery import make_id

        m = dev.match
        if m is None:
            return
        key = mac12(dev.address)
        self._record({
            "id": make_id(m.domain, key), "integration": m.domain, "name": m.title, "model": m.model,
            "manufacturer": m.manufacturer, "host": None, "port": None, "mac": key, "ids": [key],
            "source": "bluetooth",
            "props": {"address": dev.address, "encrypted": m.encrypted, "parser": m.label, **self._where(adv)},
        }, notify=True)

    def _record_beacon_finding(self, beacon: bp.Beacon, adv: Advertisement) -> None:
        from services.homehub.discovery import make_id

        self._record({
            "id": make_id("ibeacon", beacon.key), "integration": "ibeacon",
            "name": f"iBeacon {beacon.major}/{beacon.minor}", "model": "iBeacon", "manufacturer": None,
            "host": None, "port": None, "mac": None, "ids": [beacon.key], "source": "bluetooth",
            "props": {"uuid": beacon.uuid, "major": beacon.major, "minor": beacon.minor,
                      "measured_power": beacon.tx_power, **self._where(adv)},
        }, notify=False)

    # -- trackers ----------------------------------------------------------------------------
    def sync_trackers(self) -> None:
        """Phones with a registered beacon, plus whatever ``ibeacon`` drivers registered, become trackers."""
        from services.homehub.mobile_app_webhook import manager_of

        phones: dict[str, Tracker] = {}
        off: set[str] = set()  # phones whose app said the beacon is off (``ble_beacon: null``)
        self.tracker_errors = {k: v for k, v in self.tracker_errors.items() if not k.startswith("phone:")}
        mam = manager_of(self.hub)
        for entry in self.hub.config_entries.values():
            if entry.domain != "mobile_app" or entry.disabled_by:
                continue
            app_data = entry.data.get("app_data") or {}
            raw = app_data.get("ble_beacon")
            if raw is None:
                if "ble_beacon" in app_data:
                    off.add(entry.entry_id)
                continue
            try:
                beacon = bp.beacon_from_dict(raw)
            except ValueError as exc:
                self.tracker_errors[f"phone:{entry.entry_id}"] = f"{entry.title}: ble_beacon {exc}"
                continue
            other = phones.get(beacon.key)
            if other is not None:
                self.tracker_errors[f"phone:{entry.entry_id}"] = (
                    f"{entry.title}: beacon {beacon.key} is already registered by {other.name}")
                continue
            if mam is None:
                self.tracker_errors[f"phone:{entry.entry_id}"] = (
                    f"{entry.title}: the mobile_app manager is not running, so its room sensors cannot be made")
                continue
            name = str(entry.data.get("device_name") or entry.title)
            room = mam.ensure_phone_sensor(entry, "sensor", ROOM_KEY, "Room", original_icon=ROOM_ICON)
            dist = mam.ensure_phone_sensor(entry, "sensor", DISTANCE_KEY, "Room distance", original_icon=ROOM_ICON,
                                           original_device_class="distance", unit_of_measurement="m",
                                           capabilities={"state_class": "measurement"})
            tracker_ent = next((e.entity_id for e in self.hub.entities.values()
                                if e.config_entry_id == entry.entry_id and e.domain == "device_tracker"), None)
            old = self.trackers.get(beacon.key)
            t = Tracker(beacon.key, "phone", entry.entry_id, name, beacon.tx_power, room.entity_id, dist.entity_id,
                        tracker_ent)
            if old is not None and old.entry_id == entry.entry_id:
                t.state, t.last_payload, t.last_published = old.state, old.last_payload, old.last_published
            phones[beacon.key] = t
        for key, t in list(self.trackers.items()):
            if t.kind == "phone" and key not in phones:
                self.trackers.pop(key)
                reason = ("room beacon turned off on the phone" if t.entry_id in off
                          else "the phone no longer registers a beacon")
                for eid in (t.room_entity, t.distance_entity):
                    if eid and self.hub.entities.get(eid):
                        self.hub.publish(eid, UNAVAILABLE, {"reason": reason, "icon": ROOM_ICON})
        for key, t in phones.items():
            existing = self.trackers.get(key)
            if existing is not None and existing.kind == "ibeacon":
                self.tracker_errors[f"phone:{t.entry_id}"] = (
                    f"{t.name}: beacon {key} is already tracked as the iBeacon tracker {existing.name}")
                continue
            self.trackers[key] = t

    def add_tracker(self, tracker: Tracker) -> None:
        existing = self.trackers.get(tracker.key)
        if existing is not None and existing.entry_id != tracker.entry_id:
            raise BluetoothError(f"beacon {tracker.key} is already tracked by {existing.name}", 409)
        self.trackers[tracker.key] = tracker

    def remove_tracker(self, key: str, entry_id: str) -> None:
        t = self.trackers.get(key)
        if t is not None and t.entry_id == entry_id:
            self.trackers.pop(key)

    def resolve_tracker(self, value: str) -> str:
        """A phone's entity id (any of its entities) or a beacon id -> the beacon key."""
        text = str(value or "").strip()
        if not text:
            raise BluetoothError("Name the tracker: a phone's entity id or a beacon id (<uuid>-<major>-<minor>)")
        if "." in text and text.count("-") < 4:
            ent = self.hub.entities.get(text)
            if ent is None:
                raise BluetoothError(f"There is no entity {text!r}", 404)
            for t in self.trackers.values():
                if t.entry_id == ent.config_entry_id or text in (t.room_entity, t.distance_entity, t.tracker_entity):
                    return t.key
            raise BluetoothError(f"{text} is not a phone or tag with a registered beacon", 404)
        try:
            return bp.beacon_key(*bp.parse_beacon_key(text))
        except ValueError as exc:
            raise BluetoothError(str(exc)) from exc

    # -- the estimate ------------------------------------------------------------------------
    def models(self) -> dict[str, bp.AreaModel]:
        if self._models is None:
            self._models = bp.build_models(self.map)
        return self._models

    def tick(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        online = self.online(now)
        areas = {sid: self.scanner_area(sid) for sid in self.scanners}
        offsets = self._offsets()
        models = self.models()
        for t in list(self.trackers.values()):
            self._estimate(t, now, online, areas, offsets, models)
        self._sample_sessions(now, online)
        if now - self._last_prune >= 60.0:
            self._prune(now)

    def _estimate(self, t: Tracker, now: float, online: set[str], areas: dict[str, str | None],
                  offsets: dict[str, float], models: dict[str, bp.AreaModel]) -> None:
        sight = self.sightings.get(t.key)
        readings = sight.current(now, self.params.reading_window_s, online) if sight else {}
        cand = bp.choose(readings, areas, offsets, models, online, self.params) if readings else None
        st = bp.step(t.state, cand, now=now, heard=bool(readings),
                     last_heard=sight.last_heard if sight else None, params=self.params)
        if readings:
            sid = max(readings, key=lambda s: readings[s] + offsets.get(s, 0.0))
            rssi = readings[sid] + offsets.get(sid, 0.0)
            power = (t.tx_power if t.tx_power is not None else
                     sight.measured_power if sight and sight.measured_power is not None else
                     (self.settings.get(sid) or {}).get("ref_power", bp.DEFAULT_REF_POWER))
            st.scanner_id, st.rssi, st.scanners_heard = sid, round(rssi, 1), len(readings)
            st.distance_m = bp.distance_m(rssi, float(power), self.params.path_loss_n)
        self._publish(t, now, sight)

    def _publish(self, t: Tracker, now: float, sight: bp.Sighting | None) -> None:
        st = t.state
        payload = (st.status, st.area_id)
        changed = payload != t.last_payload
        if not changed and now - t.last_published < self.params.publish_min_s:
            return
        if not changed and st.status != "room":
            return
        if st.status == "room" and st.area_id:
            area = self.hub.areas.get(st.area_id)
            state = area.name if area is not None else st.area_id
        else:
            state = "not_home" if st.status == "not_home" else UNKNOWN
        sc = self.scanners.get(st.scanner_id or "")
        attrs = {"area_id": st.area_id, "distance_m": st.distance_m if st.status == "room" else None,
                 "rssi": st.rssi if st.status == "room" else None,
                 "scanner": (sc.name if sc else st.scanner_id) if st.status == "room" else None,
                 "scanners_heard": st.scanners_heard if st.status == "room" else 0,
                 "last_seen": self._iso(sight.last_heard) if sight and sight.last_heard is not None else None,
                 "source": "ble", "method": st.method, "confidence": round(st.confidence, 3),
                 "icon": ROOM_ICON}
        if t.room_entity:
            self.hub.publish(t.room_entity, state, attrs)
        if t.distance_entity:
            dist = st.distance_m if st.status == "room" and st.distance_m is not None else UNKNOWN
            self.hub.publish(t.distance_entity, dist, {"unit_of_measurement": "m", "device_class": "distance",
                                                       "state_class": "measurement", "icon": ROOM_ICON,
                                                       "source": "ble"})
        t.last_published = now
        t.last_payload = payload
        if changed and t.kind == "phone" and t.tracker_entity:
            self._update_persons(t.tracker_entity)

    def _update_persons(self, tracker_entity: str) -> None:
        mgr = getattr(self.hub, "person_manager", None)
        if mgr is None:
            return
        for p in mgr.persons:
            if tracker_entity in p["device_trackers"]:
                try:
                    mgr.update(p)
                except Exception:
                    log.exception("updating %s with its room failed", p.get("entity_id"))

    def person_room(self, device_trackers: list[str]) -> dict[str, Any]:
        """``{room, room_area_id}`` from the person's phones' room sensors (freshest wins); ``{}`` with none."""
        entries = {self.hub.entities[t].config_entry_id for t in device_trackers if t in self.hub.entities}
        mine = [t for t in self.trackers.values() if t.kind == "phone" and t.entry_id in entries]
        if not mine:
            return {}
        best = None
        for t in mine:
            if t.state.status != "room" or not t.state.area_id:
                continue
            sight = self.sightings.get(t.key)
            heard = sight.last_heard if sight and sight.last_heard is not None else float("-inf")
            if best is None or heard > best[0]:
                best = (heard, t.state.area_id)
        if best is None:
            return {"room": None, "room_area_id": None}
        area = self.hub.areas.get(best[1])
        return {"room": area.name if area else best[1], "room_area_id": best[1]}

    def entity_abilities(self, entity_id: str) -> list[str]:
        for t in self.trackers.values():
            if entity_id in (t.room_entity, t.distance_entity):
                return ["presence_room"]
        return []

    # -- the room map --------------------------------------------------------------------------
    def start_session(self, area_id: Any, tracker: Any, duration_s: Any = None) -> dict[str, Any]:
        area = self.hub.area_id_by_name(str(area_id or "")) if area_id else None
        if area is None:
            raise BluetoothError(f"There is no area {area_id!r}", 404)
        duration = SESSION_DEFAULT_S if duration_s is None else duration_s
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) \
                or not SESSION_MIN_S <= duration <= SESSION_MAX_S:
            raise BluetoothError(f"duration_s must be {SESSION_MIN_S} to {SESSION_MAX_S} seconds, not {duration!r}")
        key = self.resolve_tracker(str(tracker or ""))
        now = self.clock()
        if any(s.status == "recording" and s.key == key for s in self.sessions.values()):
            raise BluetoothError(f"{tracker} is already being recorded; wait for that session or cancel it", 409)
        sight = self.sightings.get(key)
        if sight is None or sight.last_heard is None or now - sight.last_heard > HEARD_RECENTLY_S:
            raise BluetoothError(
                f"{tracker} has not been heard by any Bluetooth scanner in the last {int(HEARD_RECENTLY_S)} s. "
                "Check that its beacon is on and that a scanner is near the room.", 409)
        sid = uuid.uuid4().hex
        s = MapSession(sid, area, str(tracker), key, now, now + float(duration),
                       self._iso(now + float(duration)), window_start=now)
        self.sessions[sid] = s
        log.info("Bluetooth map: recording %s in %s for %ss", tracker, area, duration)
        return {"session_id": sid, "area_id": area, "tracker": s.tracker, "ends_at": s.ends_at}

    def session(self, session_id: str) -> dict[str, Any]:
        s = self.sessions.get(session_id)
        if s is None:
            raise BluetoothError("There is no such mapping session", 404)
        out: dict[str, Any] = {"session_id": s.session_id, "area_id": s.area_id, "tracker": s.tracker,
                               "status": s.status, "samples": len(s.samples),
                               "scanners_heard": len(s.scanners_heard),
                               "seconds_left": max(0, round(s.ends - self.clock())) if s.status == "recording" else 0}
        if s.error:
            out["error"] = s.error
        return out

    def cancel_session(self, session_id: str) -> None:
        s = self.sessions.pop(session_id, None)
        if s is None:
            raise BluetoothError("There is no such mapping session", 404)
        if s.status == "done":
            sessions = [x for x in self.map.get(s.area_id, []) if x.get("session_id") != session_id]
            if sessions:
                self.map[s.area_id] = sessions
            else:
                self.map.pop(s.area_id, None)
            self._map_changed()

    def _sample_sessions(self, now: float, online: set[str]) -> None:
        for s in list(self.sessions.values()):
            if s.status != "recording":
                if s.finished is not None and now - s.finished > SESSION_KEEP_S:
                    self.sessions.pop(s.session_id, None)
                continue
            if now - s.window_start >= SAMPLE_S:
                names = online | set(s.window)
                sample: dict[str, float | None] = {sid: s.window.get(sid) for sid in sorted(names)}
                if any(v is not None for v in sample.values()):
                    s.samples.append(sample)
                else:
                    s.empty_windows += 1
                s.window, s.window_start = {}, now
            if now >= s.ends:
                self._finish(s, now)

    def _finish(self, s: MapSession, now: float) -> None:
        s.finished = now
        if len(s.samples) < bp.MIN_SESSION_SAMPLES:
            s.status = "failed"
            s.error = (f"Only {len(s.samples)} samples were heard (at least {bp.MIN_SESSION_SAMPLES} are needed): "
                       "keep the tracker's beacon on and in the room for the whole session.")
            log.warning("Bluetooth map: session %s failed: %s", s.session_id, s.error)
            return
        scanners = sorted({sid for sample in s.samples for sid in sample})
        self.map.setdefault(s.area_id, []).append({
            "session_id": s.session_id, "recorded_at": self._iso(now), "recorded_ts": self.wall(),
            "tracker": s.tracker, "samples": s.samples, "scanners": scanners,
            "scanner_areas": {sid: self.scanner_area(sid) for sid in scanners}})
        s.status = "done"
        self._map_changed()
        log.info("Bluetooth map: %s recorded %d samples in %s", s.tracker, len(s.samples), s.area_id)

    def _map_changed(self) -> None:
        self._models = None
        self._cv = None
        if self.store is not None:
            self.store.set_kv(KV_MAP, {"version": 1, "areas": self.map})

    def clear_map(self, area_id: str | None = None) -> int:
        if area_id is None:
            n = sum(len(v) for v in self.map.values())
            self.map = {}
        else:
            if area_id not in self.map:
                raise BluetoothError(f"The area {area_id!r} has no fingerprints", 404)
            n = len(self.map.pop(area_id))
        self._map_changed()
        return n

    def stale_reason(self) -> str | None:
        if not self.map:
            return None
        now = self.clock()
        online = self.online(now)
        mapped: set[str] = set()
        snapshot: dict[str, str | None] = {}
        for sessions in self.map.values():
            for s in sorted(sessions, key=lambda x: x.get("recorded_ts") or 0):
                mapped.update(s.get("scanners") or [])
                snapshot.update(s.get("scanner_areas") or {})
        reasons = []
        for sid in sorted(online - mapped):
            reasons.append(f"scanner {self._scanner_name(sid)} was added after the rooms were mapped")
        for sid in sorted(mapped):
            if sid in snapshot and sid in self.scanners and self.scanner_area(sid) != snapshot[sid]:
                reasons.append(f"scanner {self._scanner_name(sid)} moved to another area after mapping")
            elif sid not in online:
                reasons.append(f"scanner {self._scanner_name(sid)} is not hearing anything; rooms are scored without it")
        return "; ".join(reasons) + "; map the rooms again" if reasons else None

    def _scanner_name(self, sid: str) -> str:
        sc = self.scanners.get(sid)
        return sc.name if sc else str((self.settings.get(sid) or {}).get("name") or sid)

    def map_view(self) -> dict[str, Any]:
        if self._cv is None:
            self._cv = bp.cross_validate(self.map)
        cv = self._cv
        models = self.models()
        area_ids = list(self.hub.areas) + [a for a in self.map if a not in self.hub.areas]
        areas = []
        for a in area_ids:
            sessions = self.map.get(a, [])
            areas.append({"area_id": a, "name": self.hub.areas[a].name if a in self.hub.areas else None,
                          "samples": sum(len(s.get("samples") or []) for s in sessions), "sessions": len(sessions),
                          "last_recorded": max((s.get("recorded_at") for s in sessions), default=None),
                          "accuracy": cv["per_area"].get(a)})
        return {"method": "fingerprint" if len(models) >= 2 else "nearest_scanner", "areas": areas,
                "scanners": [self.scanner_public(sid) for sid in sorted(set(self.scanners) | set(self.settings))],
                "confusion": cv["confusion"], "overall_accuracy": cv["overall"], "stale_reason": self.stale_reason()}

    # -- views -----------------------------------------------------------------------------
    def _iso(self, mono: float) -> str:
        return datetime.fromtimestamp(self.wall() - (self.clock() - mono), UTC).isoformat()

    def _prune(self, now: float, force: bool = False) -> None:
        self._last_prune = now
        for addr in [a for a, d in self.devices.items() if now - d.last_seen > DEVICE_EXPIRE_S]:
            self.devices.pop(addr, None)
        if force and len(self.devices) >= MAX_DEVICES:
            for addr in sorted(self.devices, key=lambda a: self.devices[a].last_seen)[:MAX_DEVICES // 10]:
                self.devices.pop(addr, None)
        tracked = set(self.trackers) | {s.key for s in self.sessions.values()}
        for key in [k for k, s in self.sightings.items()
                    if k not in tracked and (s.last_heard is None or now - s.last_heard > DEVICE_EXPIRE_S)]:
            self.sightings.pop(key, None)

    def status(self) -> dict[str, Any]:
        now = self.clock()
        trackers = []
        for t in self.trackers.values():
            sight = self.sightings.get(t.key)
            trackers.append({"key": t.key, "kind": t.kind, "name": t.name, "entry_id": t.entry_id,
                             "room_entity": t.room_entity, "distance_entity": t.distance_entity,
                             "state": t.state.status, "area_id": t.state.area_id, "method": t.state.method,
                             "confidence": t.state.confidence,
                             "last_seen": self._iso(sight.last_heard) if sight and sight.last_heard else None})
        return {"host_adapter": dict(self.host_status),
                "scanners": [self.scanner_public(sid) for sid in sorted(set(self.scanners) | set(self.settings))],
                "devices_heard": sum(1 for d in self.devices.values() if now - d.last_seen <= 300),
                "trackers": trackers, "tracker_errors": sorted(self.tracker_errors.values()),
                "parsers": parsers.status(), "presence": self.params.public(),
                "map": {"areas": len(self.map), "method": "fingerprint" if len(self.models()) >= 2
                        else "nearest_scanner"}}

    def devices_view(self, seconds: float = 300.0) -> list[dict[str, Any]]:
        now = self.clock()
        known = {}
        for e in self.hub.config_entries.values():
            if e.unique_id:
                known[norm_address(e.unique_id)] = e.entry_id
        out = []
        for d in self.devices.values():
            if now - d.last_seen > seconds:
                continue
            out.append({
                "address": d.address, "name": d.name, "last_seen": self._iso(d.last_seen),
                "rssi": {sid: rssi for sid, (rssi, t) in d.rssi.items() if now - t <= seconds},
                "parser": d.match.public() if d.match else None, "ibeacon": d.beacon,
                "tracked": d.beacon in self.trackers if d.beacon else False,
                "entry_id": known.get(d.address),
            })
        out.sort(key=lambda x: max(x["rssi"].values(), default=-999), reverse=True)
        return out


# -- the host adapter (BlueZ) ------------------------------------------------------------------

#: Passive scanning needs at least one pattern; these match the flags every advertiser sends.
PASSIVE_FLAGS = (b"\x02", b"\x06", b"\x1a", b"\x1e", b"\x04", b"\x05", b"\x0a", b"\x12", b"\x16", b"\x18")


async def bluez_adapters() -> dict[str, dict[str, Any]]:
    """``{hciN: {address, powered, monitors}}`` from BlueZ's object manager on the system bus."""
    from dbus_fast import BusType, Message
    from dbus_fast.aio import MessageBus

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        reply = await bus.call(Message(destination="org.bluez", path="/", member="GetManagedObjects",
                                       interface="org.freedesktop.DBus.ObjectManager"))
        if reply.message_type.name == "ERROR":
            raise BluetoothError(f"BlueZ answered {reply.error_name}: {reply.body}", 503)
        out = {}
        for path, ifaces in reply.body[0].items():
            ad = ifaces.get("org.bluez.Adapter1")
            if ad is None:
                continue
            out[path.rsplit("/", 1)[-1]] = {
                "address": str(ad["Address"].value), "powered": bool(ad["Powered"].value),
                "monitors": "org.bluez.AdvertisementMonitorManager1" in ifaces}
        return out
    finally:
        bus.disconnect()


class BlueZScanner:
    """Scans the configured host adapter with bleak, restarting it when it fails or goes quiet."""

    def __init__(self, manager: BluetoothManager, configured: str, mode: str) -> None:
        self.manager = manager
        self.configured = configured
        self.mode = mode
        self.scanner_id: str | None = None

    async def resolve(self) -> tuple[str, dict[str, Any], str]:
        try:
            adapters = await bluez_adapters()
        except BluetoothError:
            raise
        except Exception as exc:
            raise BluetoothError(
                f"cannot reach BlueZ on the system bus ({type(exc).__name__}: {exc}). The container needs "
                "/run/dbus/system_bus_socket mounted and, with SELinux, security_opt label=disable", 503) from exc
        want = self.configured.lower()
        for name, info in adapters.items():
            if want in (name.lower(), info["address"].lower()):
                break
        else:
            have = ", ".join(f"{n} ({i['address'][:8]}...)" for n, i in sorted(adapters.items())) or "none"
            raise BluetoothError(f"{ADAPTER_ENV}={self.configured} matches no adapter BlueZ has (it has: {have})", 503)
        if not info["powered"]:
            raise BluetoothError(f"adapter {name} is powered off (bluetoothctl: select it, then power on)", 503)
        mode = self.mode
        if mode == "passive" and not info["monitors"]:
            raise BluetoothError("passive scanning needs BlueZ advertisement monitors (run bluetoothd with "
                                 "--experimental); set the scan mode to active or auto", 503)
        if mode == "auto":
            mode = "passive" if info["monitors"] else "active"
        return name, info, mode

    async def run(self) -> None:
        attempt = 0
        status = self.manager.host_status
        while True:
            try:
                name, info, mode = await self.resolve()
                why = ("BlueZ offers advertisement monitors" if mode == "passive" and self.mode == "auto" else
                       "BlueZ offers no advertisement monitors (bluetoothd without --experimental)"
                       if self.mode == "auto" else "set by " + MODE_ENV)
                status.update({"adapter": name, "address_prefix": info["address"][:8], "mode": mode,
                               "mode_reason": why, "reason": None})
                await self._scan(name, info, mode)
                attempt = 0
            except asyncio.CancelledError:
                if self.scanner_id:
                    self.manager.scanner_gone(self.scanner_id, "stopped")
                raise
            except Exception as exc:
                msg = str(exc) if isinstance(exc, BluetoothError) else f"{type(exc).__name__}: {exc}"
                status["reason"] = msg
                if self.scanner_id:
                    self.manager.scanner_gone(self.scanner_id, msg)
                delay = BLUEZ_RETRY_S[min(attempt, len(BLUEZ_RETRY_S) - 1)]
                (log.warning if attempt == 0 else log.debug)("Bluetooth host adapter: %s; retry in %ss", msg, delay)
                attempt += 1
                await asyncio.sleep(delay)

    async def _scan(self, name: str, info: dict[str, Any], mode: str) -> None:
        from bleak import BleakScanner

        mgr = self.manager
        sid = f"bluez_{mac12(info['address'])}"
        self.scanner_id = sid
        sc = mgr.add_scanner(sid, "bluez", f"Host adapter {name}", source=name)
        sc.mode = mode

        def on_adv(device: Any, adv: Any) -> None:
            mgr.ingest(Advertisement(
                address=device.address, rssi=int(adv.rssi), scanner_id=sid, time=mgr.clock(), name=adv.local_name,
                manufacturer_data=dict(adv.manufacturer_data), service_data=dict(adv.service_data),
                service_uuids=list(adv.service_uuids), tx_power=adv.tx_power))

        bluez: dict[str, Any] = {"adapter": name}
        if mode == "active":
            # Every advertisement, LE only: BlueZ otherwise reports a device again only when something changed,
            # which starves the RSSI smoothing, and a BR/EDR inquiry would interrupt the LE scan.
            bluez["filters"] = {"DuplicateData": True, "Transport": "le"}
        if mode == "passive":
            from bleak.args.bluez import OrPattern
            from bleak.assigned_numbers import AdvertisementDataType

            bluez["or_patterns"] = [OrPattern(0, AdvertisementDataType.FLAGS, f) for f in PASSIVE_FLAGS]
        scanner = BleakScanner(on_adv, scanning_mode=mode, bluez=bluez)
        await scanner.start()
        started = mgr.clock()
        log.info("Bluetooth: scanning on %s (%s)", name, mode)
        try:
            while True:
                await asyncio.sleep(30)
                last = sc.last_seen if sc.last_seen is not None else started
                if mgr.clock() - last > BLUEZ_WATCHDOG_S:
                    raise BluetoothError(f"{name} heard nothing for {int(BLUEZ_WATCHDOG_S)} s; restarting the scan")
        finally:
            with contextlib.suppress(Exception):
                await scanner.stop()


# -- ESPHome Bluetooth proxies ---------------------------------------------------------------

def attach_esphome_proxy(hub: Hub, driver: Any, client: Any, device_info: Any, api_version: Any) -> Callable[[], None] | None:
    """Subscribe to a node's advertisements when it is a Bluetooth proxy; returns the unsubscribe, or None.

    Nothing is sent to the node but the subscription: its scan mode and settings are its own YAML's.
    """
    mgr: BluetoothManager | None = getattr(hub, "bluetooth", None)
    if mgr is None or device_info is None:
        return None
    from aioesphomeapi import BluetoothProxyFeature

    try:
        flags = int(device_info.bluetooth_proxy_feature_flags_compat(api_version))
    except Exception:
        flags = 0
    if not flags & int(BluetoothProxyFeature.PASSIVE_SCAN):
        return None
    mac = mac12(getattr(device_info, "bluetooth_mac_address", "") or getattr(device_info, "mac_address", ""))
    sid = f"esphome_{mac}"
    device_id = next((e.device_id for e in driver.entities if e.device_id), None)
    if device_id is None:
        device_id = next((d.id for d in hub.devices.values() if driver.entry.entry_id in d.config_entries), None)
    name = getattr(device_info, "friendly_name", None) or getattr(device_info, "name", None) or sid
    sc = mgr.add_scanner(sid, "esphome", str(name), source=driver.entry.entry_id, device_id=device_id)
    sc.mode = "raw" if flags & int(BluetoothProxyFeature.RAW_ADVERTISEMENTS) else "parsed"

    if flags & int(BluetoothProxyFeature.RAW_ADVERTISEMENTS):
        from bluetooth_data_tools import int_to_bluetooth_address, parse_advertisement_data_bytes

        def on_raw(resp: Any) -> None:
            t = mgr.clock()
            for a in resp.advertisements:
                local_name, uuids, service_data, manufacturer_data, tx_power = parse_advertisement_data_bytes(a.data)
                mgr.ingest(Advertisement(int_to_bluetooth_address(a.address), int(a.rssi), sid, t, local_name,
                                         dict(manufacturer_data), dict(service_data), list(uuids), tx_power))

        unsub = client.subscribe_bluetooth_le_raw_advertisements(on_raw)
    else:
        from bluetooth_data_tools import int_to_bluetooth_address

        def on_adv(a: Any) -> None:
            mgr.ingest(Advertisement(int_to_bluetooth_address(a.address), int(a.rssi), sid, mgr.clock(), a.name,
                                     dict(a.manufacturer_data), dict(a.service_data), list(a.service_uuids)))

        unsub = client.subscribe_bluetooth_le_advertisements(on_adv)
    log.info("Bluetooth: ESPHome proxy %s (%s advertisements)", name, sc.mode)

    def detach() -> None:
        mgr.scanner_gone(sid, "disconnected")
        with contextlib.suppress(Exception):
            unsub()

    return detach
