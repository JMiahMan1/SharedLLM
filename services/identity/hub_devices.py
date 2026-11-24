"""Shared devices HomeHub knows, as identity ``Device`` rows: voice satellites, speakers, TVs.

A phone names its owner by signing in; a voice satellite never can. So every
voice satellite and speaker/TV media player HomeHub knows is copied here
(``sync``, from HomeHub's ``GET /hub/voice/devices``) and an admin assigns each
one to a person, or leaves it shared, and may give it a room. A spoken request
from a satellite then resolves in this order (docs/VOICE_RECOGNITION.md):

1. the speaker its voice identifies;
2. else the person the satellite is assigned to (``owner_for``);
3. else the default user.

Each row is keyed by the device's stable hardware identity (``hardware_id``:
its MAC, else HomeHub's device id), never by its name, so renaming a satellite
or its entity keeps its assignment. Nothing here is hardcoded: the list is
whatever HomeHub reports.

``owner_for`` is called on every spoken request, so it is cached in process for
``CACHE_TTL_S`` and the cache is cleared whenever an assignment or a sync
changes something.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from services.identity import device_archive
from services.identity.models import Device, User

log = logging.getLogger("identity.hub_devices")

#: The kinds a hub device can be (a satellite with a speaker is a voice_satellite).
HUB_KINDS = ("voice_satellite", "speaker", "tv")
KEY_PREFIX = "hub:"
#: How long a satellite's assignment is trusted before it is read again; any
#: change made here clears it at once.
CACHE_TTL_S = 30.0
#: How often identity asks HomeHub for its devices.
SYNC_INTERVAL_S = 600.0

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict | None]] = {}


class HubDeviceError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def entity_list(device: Device) -> list[str]:
    try:
        ids = json.loads(device.entity_ids or "[]")
    except (TypeError, ValueError):
        return []
    return [str(i) for i in ids if isinstance(i, str)] if isinstance(ids, list) else []


def is_hub_device(device: Device) -> bool:
    return bool(device.hardware_id) and device.device_key.startswith(KEY_PREFIX)


def _clean(value: Any, limit: int = 120) -> str | None:
    text = str(value).strip()[:limit] if isinstance(value, (str, int, float)) else ""
    return text or None


def _parse(item: Any) -> dict | None:
    """One device from HomeHub, or None when it is not one we can key."""
    if not isinstance(item, dict):
        return None
    hardware_id = _clean(item.get("hardware_id"), 80)
    kind = item.get("kind")
    if not hardware_id or kind not in HUB_KINDS:
        return None
    entities = [e for e in (item.get("entity_ids") or []) if isinstance(e, str) and "." in e][:20]
    if not entities:
        return None
    return {
        "hardware_id": hardware_id.lower(),
        "hub_device_id": _clean(item.get("hub_device_id"), 80),
        "kind": kind,
        "label": _clean(item.get("name")) or entities[0],
        "hub_area": _clean(item.get("area")),
        "entity_ids": sorted(set(entities)),
        "manufacturer": _clean(item.get("manufacturer")),
        "model": _clean(item.get("model")),
    }


def sync(session: Session, devices: list[Any]) -> dict[str, int]:
    """Copy HomeHub's devices in. New ones arrive shared and with no room; an
    existing one keeps its assignment and room, and only what HomeHub owns
    (name, kind, area, entities, model) is refreshed. A device HomeHub no
    longer lists is left alone: the archive sweep retires it if it stays gone.
    """
    now = datetime.now().isoformat()  # the same clock as device_archive
    added = updated = 0
    seen: set[str] = set()
    for raw in devices:
        item = _parse(raw)
        if item is None or item["hardware_id"] in seen:
            continue
        seen.add(item["hardware_id"])
        key = KEY_PREFIX + item["hardware_id"]
        row = session.exec(select(Device).where(Device.device_key == key)).first()
        if row is None:
            row = Device(device_key=key, kind=item["kind"], registered_by="hub", owner_username=None)
            added += 1
        else:
            updated += 1
        row.hardware_id = item["hardware_id"]
        row.hub_device_id = item["hub_device_id"]
        row.kind = item["kind"]
        row.label = item["label"]
        row.hub_area = item["hub_area"]
        row.entity_ids = json.dumps(item["entity_ids"])
        row.manufacturer = item["manufacturer"] or row.manufacturer
        row.model = item["model"] or row.model
        row.revoked = False
        device_archive.seen(session, row, now)
        session.add(row)
    session.commit()
    clear_cache()
    return {"added": added, "updated": updated, "reported": len(seen)}


def read(device: Device, names: dict[str, str]) -> dict[str, Any]:
    return {
        "device_key": device.device_key,
        "hardware_id": device.hardware_id,
        "hub_device_id": device.hub_device_id,
        "kind": device.kind,
        "label": device.label,
        "entity_ids": entity_list(device),
        "owner_username": device.owner_username,
        "owner_name": names.get(device.owner_username or "") if device.owner_username else None,
        "assignment": "person" if device.owner_username else "shared",
        "room": device.room,
        "hub_area": device.hub_area,
        "manufacturer": device.manufacturer,
        "model": device.model,
        "last_seen_at": device.last_seen_at,
        "archived_at": device.archived_at,
    }


def listing(session: Session) -> list[dict[str, Any]]:
    names = {u.username: (u.display_name or u.username) for u in session.exec(select(User)).all()}
    rows = [d for d in session.exec(select(Device).where(Device.kind.in_(HUB_KINDS))).all() if is_hub_device(d)]  # type: ignore[attr-defined]
    order = {k: i for i, k in enumerate(HUB_KINDS)}
    rows.sort(key=lambda d: (order.get(d.kind, 9), (d.label or "").lower()))
    return [read(d, names) for d in rows]


def assign(session: Session, device_key: str, body: dict[str, Any]) -> tuple[Device, list[dict[str, str]]]:
    """Set a hub device's person (``owner_username``; None or "" is shared) and/or ``room``."""
    row = session.exec(select(Device).where(Device.device_key == device_key)).first()
    if row is None or not is_hub_device(row):
        raise HubDeviceError("No such shared device.", 404)
    changes = []
    if "owner_username" in body:
        owner = body.get("owner_username")
        owner = str(owner).strip().lower() if owner else None
        if owner:
            person = session.exec(select(User).where(User.username == owner)).first()
            if person is None:
                raise HubDeviceError("No such person.", 422)
            if person.is_system_default:
                raise HubDeviceError("Leave it shared instead: shared devices already answer as the default user.", 422)
        if owner != row.owner_username:
            changes.append({"field": "owner_username", "change": f"{row.owner_username or 'shared'} -> {owner or 'shared'}"})
            row.owner_username = owner
    if "room" in body:
        room = _clean(body.get("room"), 60)
        if room != row.room:
            changes.append({"field": "room", "change": f"{row.room or '-'} -> {room or '-'}"})
            row.room = room
    session.add(row)
    session.commit()
    clear_cache()
    return row, changes


def owner_for(session: Session, entity_id: str | None) -> dict | None:
    """The hub device an entity belongs to, with its assignment; None when unknown.

    ``username`` is the assigned person, or None when the device is shared.
    """
    entity = (entity_id or "").strip()
    if not entity:
        return None
    now = time.monotonic()
    with _lock:
        hit = _cache.get(entity)
        if hit and now - hit[0] < CACHE_TTL_S:
            return dict(hit[1]) if hit[1] else None
    found = None
    pattern = f'%"{entity}"%'
    for row in session.exec(select(Device).where(Device.entity_ids.like(pattern))).all():  # type: ignore[attr-defined]
        if is_hub_device(row) and not row.revoked and entity in entity_list(row):
            person = (
                session.exec(select(User).where(User.username == row.owner_username)).first()
                if row.owner_username else None
            )
            found = {
                "device_key": row.device_key,
                "hardware_id": row.hardware_id,
                "kind": row.kind,
                "label": row.label,
                "room": row.room or row.hub_area,
                "username": person.username if person else None,
                "name": (person.display_name or person.username) if person else None,
                "family_role": getattr(person, "family_role", None) if person else None,
            }
            break
    with _lock:
        _cache[entity] = (now, found)
    return dict(found) if found else None


def fetch_from_hub() -> list[dict[str, Any]]:
    """HomeHub's voice satellites, speakers and TVs; HubDeviceError when it cannot say."""
    from services.identity.music_assistant import HubUnavailable, _hub_login, _hub_request

    hub_url, secret = _hub_login()
    if not hub_url:
        raise HubDeviceError("HomeHub is not set up (HOMEHUB_URL).", 503)
    try:
        body = _hub_request(hub_url, secret, "/hub/voice/devices")
    except HubUnavailable as exc:
        raise HubDeviceError(str(exc), 502) from None
    devices = body.get("devices") if isinstance(body, dict) else None
    if not isinstance(devices, list):
        raise HubDeviceError("HomeHub gave no device list.", 502)
    return devices


async def run(engine: Any) -> None:
    """Sync from HomeHub at start and every ``SYNC_INTERVAL_S``; a failure is logged, never fatal."""
    import asyncio

    await asyncio.sleep(20)
    while True:
        try:
            devices = await asyncio.to_thread(fetch_from_hub)
            with Session(engine) as session:
                result = sync(session, devices)
            log.info("[hub-devices] synced from HomeHub: %s", result)
        except HubDeviceError as exc:
            log.warning("[hub-devices] could not sync from HomeHub: %s", exc)
        except Exception as exc:  # the next sweep tries again
            log.warning("[hub-devices] sync failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(SYNC_INTERVAL_S)
