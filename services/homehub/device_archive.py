"""Archiving devices that stopped answering long ago, and deleting them once they are clearly gone.

A device unplugged for good (a bulb thrown away, a phone replaced, a TV sold) used to stay set up forever:
retried every minute, warned about once an hour, listed as needing attention, offered to voice and to Jarvis's
device lists. Here:

**Last contact** is tracked per config entry and per device and kept in the store (kv ``device_contact``), so a
restart never forgets it. Contact is the device itself answering, never the hub guessing:

- an entry that is loaded with at least one entity a driver has published a live value for since the hub
  started (not ``unavailable`` or ``unknown``): its connection or its last poll worked, or its push arrived;
- a device of an entry shared by many devices (``DEVICE_DOMAINS``: Matter nodes, Cast devices) the same way,
  on its own entities only;
- a driver that knows better says so (``Driver.last_contact()``; the companion app's phones use the time the
  phone itself last reported, not Jarvis's location relay, which outlives a replaced phone).

The first time an entry or device has no record (just after this shipped), the restored states say when it was
last heard from: the newest live value's ``last_updated``, else when it turned unavailable. With no evidence at
all the clock starts now, so nothing is archived on a guess.

**Archive** after ``archive_after_days`` (90) without contact, for the integrations in ``ENTRY_DOMAINS`` and
``DEVICE_DOMAINS`` only (devices on the LAN, Bluetooth, phones; not cloud accounts, weather or helpers). An
archived entry is never set up or retried (``Runtime.start_entry``), so it logs no warnings and raises no
repair notices; its entities are unavailable and hidden (``Hub.is_hidden``: out of voice, Jarvis's device lists
and caches, the dashboard), and a command naming one fails with "device archived" (``Hub.archived_reason``).
Its configuration, area, names, credentials and history stay. Admins get one notice per sweep that archived
anything, naming them, never one per retry.

**Restore** happens by itself when the device shows up again: discovery hears it announce (mDNS, SSDP, the
neighbour table; matched by an id it reports, never by an address alone, since an old address may be someone
else's now), a shared entry's driver publishes a live value for it, or a phone reports. An admin can restore
one too. Restoring keeps everything and starts the entry again.

**Delete** ``delete_after_days`` (365) after it was archived, if it never came back: the entry (or device) with
its devices, entities, last states, history rows and statistics, and the sealed credentials in the entry's data.
Every deletion is written to the audit list (kv ``device_archive_audit``) and admins get one summary notice.
The deletion pass runs once a day; ``preview()`` is the dry run of both passes and changes nothing.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from services.homehub.core import UNAVAILABLE, UNKNOWN, ConfigEntry, DeviceEntry, HubError, utcnow

log = logging.getLogger("homehub.device_archive")

KV_SETTINGS = "device_archive_settings"
KV_CONTACT = "device_contact"
KV_AUDIT = "device_archive_audit"
KV_LAST_DELETE = "device_archive_last_delete"

#: The defaults an admin may change (``PUT /hub/archive/settings``).
DEFAULTS: dict[str, Any] = {"enabled": True, "archive_after_days": 90, "delete_after_days": 365}
ARCHIVE_DAYS_RANGE = (7, 3650)
DELETE_DAYS_RANGE = (30, 3650)

#: Integrations archived a whole entry at a time: one entry is one device (or one device and its parts).
ENTRY_DOMAINS = frozenset({
    "esphome", "tasmota", "wiz", "homekit_controller", "roku", "webostv", "samsungtv", "androidtv_remote",
    "dlna_dmr", "mobile_app", "reolink", "mjpeg",
    # Bluetooth sensors and tags (drivers/ble.py): one entry per sensor.
    "bthome", "xiaomi_ble", "qingping", "sensorpush", "thermopro", "inkbird", "ruuvitag_ble", "govee_ble", "ibeacon",
})
#: Integrations whose one entry holds many devices: each device is archived on its own, the entry keeps running.
DEVICE_DOMAINS = frozenset({"matter", "cast"})

#: How often contact is refreshed and stale devices archived.
SWEEP_S = 600.0
#: No archiving decision this soon after the hub starts: drivers are still connecting.
STARTUP_GRACE_S = 900.0
#: The deletion pass runs at most this often.
DELETE_EVERY_S = 86400.0
#: Audit records kept.
AUDIT_KEEP = 500

Notifier = Callable[[dict[str, Any]], Awaitable[bool]]


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt is not None else None


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


async def _send_notice(payload: dict[str, Any]) -> bool:
    from services.common import notify

    return await notify.send(payload)


class ArchiveError(HubError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class DeviceArchive:
    """Owns last contact, the archive and the deletion schedule for one hub."""

    def __init__(self, runtime: Any, *, notifier: Notifier | None = None,
                 clock: Callable[[], datetime] = utcnow) -> None:
        self.runtime = runtime
        self.notifier = notifier or _send_notice
        self.clock = clock
        self.entry_contact: dict[str, datetime] = {}
        self.device_contact: dict[str, datetime] = {}
        self._dirty = False
        self._started_at = clock()
        #: Since when each shared entry (``DEVICE_DOMAINS``) has been loaded, as this run saw it: its devices are
        #: judged only once it has had ``STARTUP_GRACE_S`` to hear from them (a Matter Server that was down
        #: says nothing about the nodes behind it).
        self._loaded_since: dict[str, datetime] = {}

    @property
    def hub(self) -> Any:
        return self.runtime.hub

    @property
    def store(self) -> Any:
        return self.runtime.store

    # -- settings -----------------------------------------------------------------------------
    def settings(self) -> dict[str, Any]:
        saved = self.store.get_kv(KV_SETTINGS, {}) or {}
        return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}

    def update_settings(self, body: dict[str, Any]) -> dict[str, Any]:
        unknown = sorted(set(body) - set(DEFAULTS))
        if unknown:
            raise ArchiveError(f"Unknown setting: {', '.join(unknown)}")
        out = self.settings()
        if "enabled" in body:
            if not isinstance(body["enabled"], bool):
                raise ArchiveError("enabled must be true or false")
            out["enabled"] = body["enabled"]
        for key, (lo, hi) in (("archive_after_days", ARCHIVE_DAYS_RANGE), ("delete_after_days", DELETE_DAYS_RANGE)):
            if key not in body:
                continue
            value = body[key]
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                raise ArchiveError(f"{key} must be a whole number of days from {lo} to {hi}")
            out[key] = value
        self.store.set_kv(KV_SETTINGS, out)
        self.audit("settings", None, settings=out)
        log.info("device archive settings: %s", out)
        return out

    # -- contact ------------------------------------------------------------------------------
    def load(self) -> None:
        """Read the stored contact times, then fill the gaps from the restored states (call after load_registry)."""
        saved = self.store.get_kv(KV_CONTACT, {}) or {}
        self.entry_contact = {k: d for k, v in (saved.get("entries") or {}).items() if (d := _parse(v))}
        self.device_contact = {k: d for k, v in (saved.get("devices") or {}).items() if (d := _parse(v))}
        now = self.clock()
        for entry in self.hub.config_entries.values():
            if self._eligible(entry) and entry.entry_id not in self.entry_contact:
                self.entry_contact[entry.entry_id] = self._evidence(self._entity_ids(entry=entry)) or now
                self._dirty = True
            if self._eligible(entry) and entry.domain in DEVICE_DOMAINS:
                for dev in self._devices_of(entry):
                    if dev.id not in self.device_contact:
                        self.device_contact[dev.id] = self._evidence(self._entity_ids(device=dev)) or now
                        self._dirty = True
        self.flush()

    def flush(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        self.store.set_kv(KV_CONTACT, {"entries": {k: _iso(v) for k, v in self.entry_contact.items()},
                                       "devices": {k: _iso(v) for k, v in self.device_contact.items()}})

    def _evidence(self, entity_ids: list[str]) -> datetime | None:
        """When the restored states say the device was last heard from: its newest live value, else when it went
        unavailable. None with no states at all."""
        live, gone = [], []
        for eid in entity_ids:
            st = self.hub.states.get(eid)
            if st is None:
                continue
            if st.state == UNAVAILABLE:
                gone.append(st.last_changed)
            elif st.state != UNKNOWN:
                live.append(st.last_updated)
        if live:
            return max(live)
        return max(gone) if gone else None

    def touch_entry(self, entry_id: str, when: datetime | None = None) -> None:
        when = when or self.clock()
        cur = self.entry_contact.get(entry_id)
        if cur is None or when > cur:
            self.entry_contact[entry_id] = when
            self._dirty = True

    def touch_device(self, device_id: str, when: datetime | None = None) -> None:
        when = when or self.clock()
        cur = self.device_contact.get(device_id)
        if cur is None or when > cur:
            self.device_contact[device_id] = when
            self._dirty = True

    def last_contact(self, kind: str, item_id: str) -> datetime | None:
        return (self.entry_contact if kind == "entry" else self.device_contact).get(item_id)

    def _eligible(self, entry: ConfigEntry) -> bool:
        return (entry.domain in ENTRY_DOMAINS or entry.domain in DEVICE_DOMAINS) and entry.source != "ignore" \
            and not entry.disabled_by

    def _devices_of(self, entry: ConfigEntry) -> list[DeviceEntry]:
        return [d for d in self.hub.devices.values() if entry.entry_id in d.config_entries]

    def _entity_ids(self, *, entry: ConfigEntry | None = None, device: DeviceEntry | None = None) -> list[str]:
        if device is not None:
            return [e.entity_id for e in self.hub.entities.values() if e.device_id == device.id and not e.disabled_by]
        assert entry is not None
        return [e.entity_id for e in self.hub.entities.values()
                if e.config_entry_id == entry.entry_id and not e.disabled_by]

    def _answering(self, entity_ids: list[str]) -> bool:
        """A driver has published a live value for one of these since the hub started, and it is still live."""
        live = self.hub.live
        for eid in entity_ids:
            if eid not in live:
                continue
            st = self.hub.states.get(eid)
            if st is not None and st.state not in (UNAVAILABLE, UNKNOWN):
                return True
        return False

    def _driver_contact(self, entry: ConfigEntry) -> tuple[bool, datetime | None]:
        """``(the driver says, when)``: a driver with its own idea of contact answers for its entry."""
        driver = self.hub.drivers.get(entry.entry_id)
        fn = getattr(driver, "last_contact", None) if driver is not None else None
        if not callable(fn):
            return False, None
        try:
            return True, fn()
        except Exception:
            log.exception("%s (%s): reading its last contact failed", entry.domain, entry.title)
            return True, None

    def _phone_seen(self, entry: ConfigEntry) -> datetime | None:
        """When a companion-app phone itself last reported (kept by the webhook manager across restarts)."""
        mgr = getattr(self.hub, "mobile_app_manager", None)
        seen = mgr.last_seen(entry.entry_id) if mgr is not None else None
        return datetime.fromtimestamp(float(seen), UTC) if seen else None

    def refresh(self) -> list[tuple[str, str]]:
        """Record contact for everything answering now. Answers the archived items that answered (to restore)."""
        now = self.clock()
        back: list[tuple[str, str]] = []
        for entry in list(self.hub.config_entries.values()):
            if not self._eligible(entry):
                continue
            if entry.domain in DEVICE_DOMAINS:
                if entry.state != "loaded":
                    self._loaded_since.pop(entry.entry_id, None)
                    continue
                self._loaded_since.setdefault(entry.entry_id, now)
                for dev in self._devices_of(entry):
                    if self._answering(self._entity_ids(device=dev)):
                        self.touch_device(dev.id, now)
                        if dev.archived_at:
                            back.append(("device", dev.id))
                continue
            when: datetime | None = None
            if entry.domain == "mobile_app":
                when = self._phone_seen(entry)
            if entry.archived_at:
                # Not running, so only evidence from outside the driver can say it is back (a phone's own report).
                since = _parse(entry.archived_at)
                if when is not None and since is not None and when > since:
                    self.touch_entry(entry.entry_id, when)
                    back.append(("entry", entry.entry_id))
                continue
            says, driver_when = self._driver_contact(entry)
            if says:
                when = max((w for w in (when, driver_when) if w is not None), default=None)
            elif entry.state == "loaded" and self._answering(self._entity_ids(entry=entry)):
                when = now
            if when is not None:
                self.touch_entry(entry.entry_id, when)
        return back

    # -- archiving ----------------------------------------------------------------------------
    def _seen(self, kind: str, item_id: str) -> datetime:
        return self.last_contact(kind, item_id) or self.clock()

    def candidates(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """What would be archived now (never anything answering, disabled, or already archived)."""
        now = now or self.clock()
        cutoff = now - timedelta(days=int(self.settings()["archive_after_days"]))
        out = []
        for entry in self.hub.config_entries.values():
            if not self._eligible(entry) or entry.archived_at:
                continue
            if entry.domain in DEVICE_DOMAINS:
                up = self._loaded_since.get(entry.entry_id)
                if up is None or (now - up).total_seconds() < STARTUP_GRACE_S:
                    continue  # the shared entry itself is not (long enough) up: its devices cannot be judged
                for dev in self._devices_of(entry):
                    if dev.archived_at or dev.disabled_by:
                        continue
                    seen = self._seen("device", dev.id)
                    if seen <= cutoff:
                        out.append(self._view("device", dev.id, entry=entry, device=dev))
                continue
            seen = self._seen("entry", entry.entry_id)
            if seen <= cutoff:
                out.append(self._view("entry", entry.entry_id, entry=entry))
        return out

    async def archive_stale(self) -> list[dict[str, Any]]:
        """Archive every candidate; one admin notice names them. Answers what was archived."""
        done = []
        for item in self.candidates():
            try:
                done.append(await self.archive(item["kind"], item["id"]))
            except Exception:
                log.exception("archiving %s %s failed", item["kind"], item["id"])
        if done:
            await self._notify_archived(done)
        return done

    async def archive(self, kind: str, item_id: str) -> dict[str, Any]:
        """Archive one entry or device now (the sweep, or a test). Keeps everything; stops everything."""
        now = _iso(self.clock())
        if kind == "entry":
            entry = self._entry(item_id)
            if entry.archived_at:
                return self._view("entry", item_id, entry=entry)
            entry.archived_at = now
            self.store.save_config_entry(entry)
            await self.runtime.stop_entry(item_id)
            entry.state, entry.reason, entry.needs = "not_loaded", f"Archived on {now[:10]}", None
            ids = self._entity_ids(entry=entry)
            self.audit("archived", self._view("entry", item_id, entry=entry))
            log.info("archived %s %s: no contact since %s", entry.domain, entry.title,
                     _iso(self.last_contact("entry", item_id)))
        else:
            dev = self._device(item_id)
            if dev.archived_at:
                return self._view("device", item_id, device=dev)
            dev.archived_at = now
            self.store.save_device(dev)
            ids = self._entity_ids(device=dev)
            self.audit("archived", self._view("device", item_id, device=dev))
            log.info("archived device %s: no contact since %s", dev.display_name,
                     _iso(self.last_contact("device", item_id)))
        self._mark_unavailable(ids)
        self._registry_changed(kind, item_id)
        return self._view(kind, item_id)

    def _mark_unavailable(self, entity_ids: list[str]) -> None:
        for eid in entity_ids:
            st = self.hub.states.get(eid)
            if st is not None and st.state == UNAVAILABLE:
                continue
            self.hub.publish(eid, UNAVAILABLE, dict(st.attributes) if st else {})

    def _registry_changed(self, kind: str, item_id: str) -> None:
        """Lists, caches and voice read the hidden set again (execution, the gateway's caches)."""
        bus = self.hub.bus
        if kind == "entry":
            for dev in self._devices_of(self.hub.config_entries[item_id]):
                bus.fire("device_registry_updated", {"action": "update", "device_id": dev.id})
        else:
            bus.fire("device_registry_updated", {"action": "update", "device_id": item_id})

    # -- restoring ----------------------------------------------------------------------------
    def restore(self, kind: str, item_id: str, *, why: str = "admin") -> dict[str, Any]:
        """Bring an archived entry or device back as it was; its contact clock starts again now."""
        now = self.clock()
        if kind == "entry":
            entry = self._entry(item_id)
            if not entry.archived_at:
                raise ArchiveError(f"{entry.title} is not archived", 409)
            view = self._view("entry", item_id, entry=entry)
            entry.archived_at = None
            entry.state, entry.reason = "not_loaded", None
            self.store.save_config_entry(entry)
            self.touch_entry(item_id, now)
            if not entry.disabled_by:
                self.runtime.start_entry(entry)
            log.info("restored %s %s (%s)", entry.domain, entry.title, why)
        else:
            dev = self._device(item_id)
            if not dev.archived_at:
                raise ArchiveError(f"{dev.display_name} is not archived", 409)
            view = self._view("device", item_id, device=dev)
            dev.archived_at = None
            self.store.save_device(dev)
            self.touch_device(item_id, now)
            log.info("restored device %s (%s)", dev.display_name, why)
        self.flush()
        self.audit("restored", view, why=why)
        self._registry_changed(kind, item_id)
        return {"status": "restored", "kind": kind, "id": item_id, "why": why}

    def on_sighting(self, finding: dict[str, Any]) -> list[dict[str, Any]]:
        """Discovery heard a device announce itself: restore the archived entry or device it is.

        Matched only by an id the device reports (MAC, serial, uuid, node id), never by address alone.
        """
        from services.homehub.discovery import token

        tokens = {t for v in [finding.get("mac"), *(finding.get("ids") or [])] if len(t := token(v)) >= 6}
        if not tokens:
            return []
        out = []
        for kind, item_id, ids in self._archived_tokens():
            if tokens & ids:
                with contextlib.suppress(ArchiveError):
                    out.append(self.restore(kind, item_id, why=f"rediscovered by {finding.get('source') or 'discovery'}"))
        return out

    def _archived_tokens(self) -> list[tuple[str, str, set[str]]]:
        from services.homehub.discovery import KnownIndex, token

        def toks(values: list[Any]) -> set[str]:
            return {t for v in values if v not in (None, "") and len(t := token(v)) >= 6}

        out = []
        for entry in self.hub.config_entries.values():
            if entry.archived_at:
                values: list[Any] = [entry.unique_id]
                for src in (entry.data or {}, entry.options or {}):
                    values += [src.get(k) for k in KnownIndex.ID_KEYS]
                for dev in self._devices_of(entry):
                    values += [p[1] for p in [*dev.identifiers, *dev.connections] if len(p) >= 2]
                out.append(("entry", entry.entry_id, toks(values)))
        for dev in self.hub.devices.values():
            if dev.archived_at:
                fabric = any((e := self.hub.config_entries.get(x)) is not None and e.domain == "matter"
                             for x in dev.config_entries)
                # A Matter node's MAC belongs to the LAN integration that also sees it (discovery.KnownIndex).
                pairs = dev.identifiers if fabric else [*dev.identifiers, *dev.connections]
                out.append(("device", dev.id, toks([p[1] for p in pairs if len(p) >= 2])))
        return out

    # -- deleting -----------------------------------------------------------------------------
    def due(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Archived items whose time is up and that never came back."""
        now = now or self.clock()
        keep = timedelta(days=int(self.settings()["delete_after_days"]))
        out = []
        for kind, item_id, since in self._archived():
            if since + keep > now:
                continue
            if self._came_back(kind, item_id, since):
                continue
            out.append(self._view(kind, item_id))
        return out

    def _came_back(self, kind: str, item_id: str, since: datetime) -> bool:
        seen = self.last_contact(kind, item_id)
        return seen is not None and seen > since

    async def delete_due(self) -> list[dict[str, Any]]:
        done = []
        for item in self.due():
            try:
                done.append(await self.delete(item["kind"], item["id"], why="schedule"))
            except Exception:
                log.exception("deleting archived %s %s failed", item["kind"], item["id"])
        self.store.set_kv(KV_LAST_DELETE, _iso(self.clock()))
        if done:
            await self._notify_deleted(done)
        return done

    async def delete(self, kind: str, item_id: str, *, why: str = "admin") -> dict[str, Any]:
        """Delete an archived entry or device with its entities, history, statistics and credentials."""
        from services.homehub import entries

        item = self._view(kind, item_id)
        since = _parse(item["archived_at"])
        if since is None:
            raise ArchiveError(f"{item['name']} is not archived; only archived devices are deleted", 409)
        if self._came_back(kind, item_id, since):
            raise ArchiveError(f"{item['name']} answered since it was archived; restore it instead", 409)
        entity_ids = list(item["entity_ids"])
        history = self.store.count_history(entity_ids)
        stats = self._statistics()
        stat_rows = stats.count_statistic_rows(entity_ids) if stats is not None else 0
        if kind == "entry":
            entry = self._entry(item_id)
            mgr = getattr(self.hub, "mobile_app_manager", None)
            if entry.domain == "mobile_app" and mgr is not None:
                await mgr.delete_phone(item_id)  # its webhook id then answers 410
            else:
                # The device has been gone a year: no unpairing (it cannot answer), straight removal.
                await entries.remove_entry(self.runtime, item_id, force=True, unpair=False)
            self.entry_contact.pop(item_id, None)
            for dev_id in item["device_ids"]:
                self.device_contact.pop(dev_id, None)
        else:
            await entries.remove_device(self.runtime, item_id, force=True)
            self.device_contact.pop(item_id, None)
        self._dirty = True
        purged = await self._purge_history(entity_ids)
        stat_purged = stats.purge_statistic_ids(entity_ids) if stats is not None else 0
        self.flush()
        record = {**{k: item[k] for k in ("kind", "id", "name", "domain", "label", "area", "last_seen",
                                          "archived_at", "device_ids")},
                  "entities": len(entity_ids), "history_rows": max(purged, history),
                  "statistics_rows": max(stat_purged, stat_rows), "credentials_removed": kind == "entry"}
        self.audit("deleted", record, why=why)
        log.info("deleted archived %s %s (%s): %d entities, %d history rows, %d statistics rows", kind, item["name"],
                 why, len(entity_ids), record["history_rows"], record["statistics_rows"])
        return {"status": "deleted", **record}

    def _statistics(self) -> Any:
        return getattr(self.runtime, "statistics", None)

    async def _purge_history(self, entity_ids: list[str]) -> int:
        if not entity_ids:
            return 0
        wanted = set(entity_ids)
        rec = getattr(self.runtime, "recorder", None)
        if rec is not None:
            return await rec.purge_entities(lambda eid: eid in wanted)
        return await asyncio.to_thread(self.store.purge_entities, lambda eid: eid in wanted)

    # -- views --------------------------------------------------------------------------------
    def _entry(self, entry_id: str) -> ConfigEntry:
        entry = self.hub.config_entries.get(entry_id)
        if entry is None:
            raise ArchiveError("No such integration entry", 404)
        return entry

    def _device(self, device_id: str) -> DeviceEntry:
        dev = self.hub.devices.get(device_id)
        if dev is None:
            raise ArchiveError("No such device", 404)
        return dev

    def _archived(self) -> list[tuple[str, str, datetime]]:
        out = []
        for e in self.hub.config_entries.values():
            if e.archived_at and (since := _parse(e.archived_at)):
                out.append(("entry", e.entry_id, since))
        for d in self.hub.devices.values():
            if d.archived_at and (since := _parse(d.archived_at)):
                out.append(("device", d.id, since))
        return out

    def _view(self, kind: str, item_id: str, *, entry: ConfigEntry | None = None,
              device: DeviceEntry | None = None) -> dict[str, Any]:
        from services.homehub import entries

        hub = self.hub
        if kind == "entry":
            entry = entry or self._entry(item_id)
            devices = self._devices_of(entry)
            dev0 = next((d for d in devices if not d.via_device_id), devices[0] if devices else None)
            name = entry.title or (dev0.display_name if dev0 else entry.domain)
            area_id = dev0.area_id if dev0 else None
            archived_at = entry.archived_at
            entity_ids = self._entity_ids(entry=entry)
            # Disabled entities go with the entry too.
            entity_ids = sorted({*entity_ids, *(e.entity_id for e in hub.entities.values()
                                                if e.config_entry_id == entry.entry_id)})
            device_ids = [d.id for d in devices if d.config_entries == [entry.entry_id]]
            domain = entry.domain
        else:
            device = device or self._device(item_id)
            entry = next((hub.config_entries[x] for x in device.config_entries if x in hub.config_entries), None)
            name = device.display_name or device.id
            area_id = device.area_id
            archived_at = device.archived_at
            children = [d.id for d in hub.devices.values() if d.via_device_id == device.id]
            device_ids = [device.id, *children]
            entity_ids = sorted(e.entity_id for e in hub.entities.values() if e.device_id in device_ids)
            domain = entry.domain if entry else None
        seen = self.last_contact(kind, item_id)
        since = _parse(archived_at)
        delete_on = since + timedelta(days=int(self.settings()["delete_after_days"])) if since else None
        area = hub.areas.get(area_id or "")
        return {
            "kind": kind, "id": item_id, "name": name, "domain": domain,
            "label": entries.label(domain) if domain else None,
            "entry_id": entry.entry_id if entry else None, "entry_title": entry.title if entry else None,
            "area_id": area_id, "area": area.name if area else None,
            "last_seen": _iso(seen), "archived_at": archived_at, "delete_on": _iso(delete_on),
            "entity_ids": entity_ids, "device_ids": device_ids,
        }

    def listing(self) -> dict[str, Any]:
        items = [self._view(kind, item_id) for kind, item_id, _ in self._archived()]
        for i in items:
            i["entities"] = len(i.pop("entity_ids"))
        items.sort(key=lambda i: (i["archived_at"] or "", str(i["name"]).lower()), reverse=True)
        audit = self.store.get_kv(KV_AUDIT, []) or []
        return {"settings": self.settings(), "items": items, "audit": list(reversed(audit[-50:])),
                "last_delete_run": self.store.get_kv(KV_LAST_DELETE)}

    def preview(self) -> dict[str, Any]:
        """The dry run: what the next sweep would archive and the next deletion pass would delete. Changes nothing."""
        stats = self._statistics()
        archive = self.candidates()
        for i in archive:
            i["entities"] = len(i.pop("entity_ids"))
        delete = self.due()
        for i in delete:
            ids = i.pop("entity_ids")
            i["entities"] = len(ids)
            i["history_rows"] = self.store.count_history(ids)
            i["statistics_rows"] = stats.count_statistic_rows(ids) if stats is not None else 0
        return {"settings": self.settings(), "archive": archive, "delete": delete}

    def audit(self, action: str, item: dict[str, Any] | None, **extra: Any) -> None:
        rows = list(self.store.get_kv(KV_AUDIT, []) or [])
        record = {"at": _iso(self.clock()), "action": action, **extra}
        if item is not None:
            record.update({k: item.get(k) for k in ("kind", "id", "name", "domain", "last_seen", "archived_at")
                           if k in item})
            for k in ("entities", "history_rows", "statistics_rows", "credentials_removed", "device_ids"):
                if k in item:
                    record[k] = item[k]
        rows.append(record)
        self.store.set_kv(KV_AUDIT, rows[-AUDIT_KEEP:])

    # -- telling the admins -------------------------------------------------------------------
    async def _notify(self, title: str, body: str, key: str, items: list[dict[str, Any]]) -> None:
        from services.common import notify

        digest = hashlib.sha256(",".join(sorted(i["id"] for i in items)).encode()).hexdigest()[:16]
        payload = notify.notice("@admins", "admin", title, body,
                                link=notify.link("devices", hash="devices-archived"), priority=3,
                                tag=f"admin:{key}", once=f"homehub:{key}:{digest}", kind="admin",
                                audience="parents")
        try:
            await self.notifier(payload)
        except Exception:
            log.warning("could not tell the admins: %s", title)

    @staticmethod
    def _names(items: list[dict[str, Any]]) -> str:
        names = [f"{i['name']} ({i['label']})" if i.get("label") else str(i["name"]) for i in items[:5]]
        more = f" and {len(items) - 5} more" if len(items) > 5 else ""
        return ", ".join(names) + more

    async def _notify_archived(self, items: list[dict[str, Any]]) -> None:
        days = self.settings()["archive_after_days"]
        n = len(items)
        await self._notify(
            f"{n} device{'s' if n != 1 else ''} archived",
            f"No contact for {days} days: {self._names(items)}. They are no longer retried or offered to voice. "
            "They come back by themselves if they show up again, or restore them under Devices > Setup.",
            "devices-archived", items)

    async def _notify_deleted(self, items: list[dict[str, Any]]) -> None:
        n = len(items)
        await self._notify(
            f"{n} archived device{'s' if n != 1 else ''} deleted",
            f"Archived for {self.settings()['delete_after_days']} days and never back: {self._names(items)}. "
            "Their settings, history and stored logins were removed.",
            "devices-deleted", items)

    # -- the schedule -------------------------------------------------------------------------
    async def sweep(self) -> dict[str, Any]:
        """Record contact, restore what came back and (past the startup grace) archive what is stale."""
        restored = []
        for kind, item_id in self.refresh():
            with contextlib.suppress(ArchiveError):
                restored.append(self.restore(kind, item_id, why="it answered again"))
        archived: list[dict[str, Any]] = []
        settled = (self.clock() - self._started_at).total_seconds() >= STARTUP_GRACE_S
        if settled and self.settings()["enabled"]:
            archived = await self.archive_stale()
        self.flush()
        return {"restored": restored, "archived": archived}

    async def run(self) -> None:
        """The background loop: a sweep every ``SWEEP_S``, a deletion pass once a day."""
        while True:
            await asyncio.sleep(SWEEP_S)
            try:
                await self.sweep()
            except Exception:
                log.exception("device archive sweep failed")
            try:
                last = _parse(self.store.get_kv(KV_LAST_DELETE))
                now = self.clock()
                if self.settings()["enabled"] and (last is None or (now - last).total_seconds() >= DELETE_EVERY_S) \
                        and (now - self._started_at).total_seconds() >= STARTUP_GRACE_S:
                    await self.delete_due()
            except Exception:
                log.exception("deleting archived devices failed")
