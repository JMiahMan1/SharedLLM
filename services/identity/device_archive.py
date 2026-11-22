"""Archiving registered devices (phones, tablets, PCs, satellites) that stopped checking in.

The same rule as the hub's (services/homehub/device_archive.py), for identity's own ``Device`` rows:

- **Archive** a device whose last check-in (``last_seen_at``, else when it was registered) is more than
  ``archive_after_days`` (90) old. An archived device keeps its row, owner, label and history; it leaves the
  device lists (``GET /api/user-panel/devices`` lists it only with ``include_archived``), so nothing offers it
  or acts on it.
- **Restore** happens by itself on its next check-in (``seen``), or an admin restores it.
- **Delete** ``delete_after_days`` (365) after it was archived if it never checked in since: the row (with its
  agent key), its usage events and capability reports, its unlock requests. Each deletion is an audit event.

The settings live in the ``device_archive_settings`` global setting. The pass runs once a day
(``run``); ``preview`` is its dry run.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, delete, select

from services.identity import audit
from services.identity.models import CapabilityInventory, Device, DeviceEvent, GlobalSetting, GuardUnlockRequest

log = logging.getLogger("identity.device_archive")

SETTINGS_KEY = "device_archive_settings"
DEFAULTS: dict[str, Any] = {"enabled": True, "archive_after_days": 90, "delete_after_days": 365}
ARCHIVE_DAYS_RANGE = (7, 3650)
DELETE_DAYS_RANGE = (30, 3650)
#: The pass runs this often (and first this long after startup, so a restart is not a check-in gap).
EVERY_S = 86400.0
FIRST_RUN_AFTER_S = 1800.0

Notifier = Callable[[dict[str, Any]], Awaitable[bool]]


class ArchiveError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    # The same clock as ``last_seen_at`` (naive local time, as registration writes it).
    return datetime.now()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


# -- settings ----------------------------------------------------------------------------------

def settings(session: Session) -> dict[str, Any]:
    row = session.exec(select(GlobalSetting).where(GlobalSetting.key == SETTINGS_KEY)).first()
    saved: dict[str, Any] = {}
    if row is not None:
        with contextlib.suppress(ValueError):
            loaded = json.loads(row.value or "{}")
            saved = loaded if isinstance(loaded, dict) else {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def update_settings(session: Session, body: dict[str, Any], *, actor: str = "internal") -> dict[str, Any]:
    unknown = sorted(set(body) - set(DEFAULTS))
    if unknown:
        raise ArchiveError(f"Unknown setting: {', '.join(unknown)}")
    out = settings(session)
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise ArchiveError("enabled must be true or false")
        out["enabled"] = body["enabled"]
    for key, (lo, hi) in (("archive_after_days", ARCHIVE_DAYS_RANGE), ("delete_after_days", DELETE_DAYS_RANGE)):
        if key in body:
            value = body[key]
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                raise ArchiveError(f"{key} must be a whole number of days from {lo} to {hi}")
            out[key] = value
    row = session.exec(select(GlobalSetting).where(GlobalSetting.key == SETTINGS_KEY)).first()
    if row is None:
        row = GlobalSetting(key=SETTINGS_KEY, value="{}",
                            description="Archive registered devices that stop checking in, and delete them later")
    row.value = json.dumps(out)
    session.add(row)
    audit.record(session, actor=actor, actor_kind="user" if actor != "internal" else "internal",
                 action="device_archive.settings", note=json.dumps(out), commit=False)
    session.commit()
    return out


# -- the rule ----------------------------------------------------------------------------------

def last_contact(device: Device) -> datetime | None:
    return _parse(device.last_seen_at) or _parse(device.first_seen_at)


def seen(session: Session, device: Device, when: str | None = None) -> None:
    """A check-in: ``last_seen_at`` moves, and an archived device is restored (the caller commits)."""
    device.last_seen_at = when or _now().isoformat()
    if getattr(device, "archived_at", None):
        device.archived_at = None
        audit.record(session, actor="internal", actor_kind="internal", action="device.restore",
                     target=device.owner_username, note=f"{device.device_key}: checked in again", commit=False)
        log.info("device %s checked in again; restored from the archive", device.device_key)
    session.add(device)


def view(device: Device, cfg: dict[str, Any]) -> dict[str, Any]:
    since = _parse(device.archived_at)
    delete_on = since + timedelta(days=int(cfg["delete_after_days"])) if since else None
    seen_at = last_contact(device)
    return {
        "kind": "identity", "id": device.device_key, "name": device.label or device.model or device.device_key,
        "device_kind": device.kind, "owner": device.owner_username, "model": device.model,
        "last_seen": seen_at.isoformat() if seen_at else None, "archived_at": device.archived_at,
        "delete_on": delete_on.isoformat() if delete_on else None,
    }


def stale(session: Session, now: datetime | None = None) -> list[Device]:
    now = now or _now()
    cutoff = now - timedelta(days=int(settings(session)["archive_after_days"]))
    out = []
    for d in session.exec(select(Device).where(Device.archived_at.is_(None))).all():  # type: ignore[union-attr]
        if d.revoked:
            continue
        seen_at = last_contact(d)
        if seen_at is not None and seen_at <= cutoff:
            out.append(d)
    return out


def archived(session: Session) -> list[Device]:
    return list(session.exec(select(Device).where(Device.archived_at.is_not(None))).all())  # type: ignore[union-attr]


def came_back(device: Device) -> bool:
    since, seen_at = _parse(device.archived_at), _parse(device.last_seen_at)
    return since is not None and seen_at is not None and seen_at > since


def due(session: Session, now: datetime | None = None) -> list[Device]:
    now = now or _now()
    keep = timedelta(days=int(settings(session)["delete_after_days"]))
    return [d for d in archived(session)
            if (since := _parse(d.archived_at)) is not None and since + keep <= now and not came_back(d)]


def archive(session: Session, device: Device, now: datetime | None = None) -> None:
    device.archived_at = (now or _now()).isoformat()
    session.add(device)
    audit.record(session, actor="internal", actor_kind="internal", action="device.archive",
                 target=device.owner_username,
                 note=f"{device.device_key}: no check-in since {device.last_seen_at or device.first_seen_at}",
                 commit=False)


def restore(session: Session, device_key: str, *, actor: str) -> Device:
    device = session.exec(select(Device).where(Device.device_key == device_key)).first()
    if device is None:
        raise ArchiveError("No such device", 404)
    if not device.archived_at:
        raise ArchiveError(f"{device.label or device_key} is not archived", 409)
    device.archived_at = None
    # Its clock starts again now, so the next pass does not archive it straight back.
    device.last_seen_at = _now().isoformat()
    session.add(device)
    audit.record(session, actor=actor, actor_kind="user", action="device.restore", target=device.owner_username,
                 note=f"{device_key}: restored by an admin", commit=False)
    session.commit()
    return device


def delete_device(session: Session, device_key: str, *, actor: str, why: str) -> dict[str, Any]:
    """Delete an archived device's row (its agent key with it) and what it reported."""
    device = session.exec(select(Device).where(Device.device_key == device_key)).first()
    if device is None:
        raise ArchiveError("No such device", 404)
    if not device.archived_at:
        raise ArchiveError(f"{device.label or device_key} is not archived; only archived devices are deleted", 409)
    if came_back(device):
        raise ArchiveError(f"{device.label or device_key} checked in since it was archived", 409)
    item = view(device, settings(session))
    counts = {}
    for name, model in (("events", DeviceEvent), ("capability_reports", CapabilityInventory),
                        ("unlock_requests", GuardUnlockRequest)):
        counts[name] = session.exec(delete(model).where(model.device_key == device_key)).rowcount  # type: ignore[call-overload]
    session.delete(device)
    audit.record(session, actor=actor, actor_kind="user" if actor != "internal" else "internal",
                 action="device.delete_archived", target=device.owner_username,
                 note=json.dumps({"device_key": device_key, "why": why, "archived_at": item["archived_at"],
                                  "last_seen": item["last_seen"], "agent_key_removed": bool(device.agent_key_hash),
                                  **counts}), commit=False)
    session.commit()
    log.info("deleted archived device %s (%s): %s", device_key, why, counts)
    return {"status": "deleted", **item, **counts}


def listing(session: Session) -> dict[str, Any]:
    cfg = settings(session)
    items = sorted((view(d, cfg) for d in archived(session)), key=lambda i: i["archived_at"] or "", reverse=True)
    return {"settings": cfg, "items": items}


def preview(session: Session) -> dict[str, Any]:
    cfg = settings(session)
    return {"settings": cfg, "archive": [view(d, cfg) for d in stale(session)],
            "delete": [view(d, cfg) for d in due(session)]}


# -- the daily pass ----------------------------------------------------------------------------

def run_once(session: Session, now: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
    """Archive what went quiet, delete what has been archived long enough. Answers what it did."""
    cfg = settings(session)
    if not cfg["enabled"]:
        return {"archived": [], "deleted": []}
    done = []
    for d in stale(session, now):
        archive(session, d, now)
        done.append(view(d, cfg))
    session.commit()
    gone = []
    for d in due(session, now):
        try:
            gone.append(delete_device(session, d.device_key, actor="internal", why="schedule"))
        except ArchiveError as exc:
            log.warning("not deleting %s: %s", d.device_key, exc)
    return {"archived": done, "deleted": gone}


async def _send_notice(payload: dict[str, Any]) -> bool:
    from services.common import notify

    return await notify.send(payload)


async def notify_admins(result: dict[str, list[dict[str, Any]]], notifier: Notifier | None = None) -> None:
    """One notice per pass that changed anything, naming the devices."""
    from services.common import notify

    send = notifier or _send_notice
    for key, verb in (("archived", "archived"), ("deleted", "deleted")):
        items = result.get(key) or []
        if not items:
            continue
        names = ", ".join(str(i["name"]) for i in items[:5]) + (f" and {len(items) - 5} more" if len(items) > 5 else "")
        body = (f"Not checked in for a long time: {names}. Restore one under Devices > Setup > Archived devices."
                if key == "archived" else f"Archived a year and never back: {names}. Their records were removed.")
        payload = notify.notice(
            "@admins", "admin", f"{len(items)} registered device{'s' if len(items) != 1 else ''} {verb}", body,
            link=notify.link("devices", hash="devices-archived"), priority=3, tag=f"admin:identity-devices-{key}",
            once=f"identity:devices-{key}:" + ",".join(sorted(i["id"] for i in items))[:150], kind="admin",
            audience="parents")
        try:
            await send(payload)
        except Exception:
            log.warning("could not tell the admins about %s devices", key)


async def run(engine: Any, *, notifier: Notifier | None = None) -> None:
    """The background loop identity's lifespan starts."""
    await asyncio.sleep(FIRST_RUN_AFTER_S)
    while True:
        try:
            with Session(engine) as session:
                result = run_once(session)
            await notify_admins(result, notifier)
        except Exception:
            log.exception("the device archive pass failed")
        await asyncio.sleep(EVERY_S)
