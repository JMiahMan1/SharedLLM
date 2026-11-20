"""Quiet morning digest for Jarvis Garden (Phase 2).

One digest per family member per day, sent at ``daily_digest_time`` from
``garden_profile``. Each digest lists the member's due/overdue tasks with
personal plant names, plus inline push callback buttons
(``garden_done`` / ``garden_snooze``). A ``once`` key dedupes restarts and
replicas so a digest is never sent twice for the same day.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from services.common import notify
from services import config
from services.garden.models import (
    CareLog,
    CareRule,
    GardenProfile,
    Location,
    Plant,
    utcnow,
)

log = logging.getLogger("garden.notices")

#: Once-key dedup window: a digest is sent at most once per day per member.
ONCE_SECONDS = 24 * 3600

#: How often the digest loop checks whether it's time to send.
LOOP_INTERVAL_S = 60


def _once_key(member: str, day: str) -> str:
    return f"garden:digest:{member}:{day}"


def build_digest(member: str, due_tasks: list[dict]) -> dict | None:
    """Build a morning digest dict for one member, or ``None`` if nothing due.

    Each action button carries real ``params`` (``plant_id`` or ``plant_ids``)
    so the push callback knows which plant(s) to act on.
    """
    if not due_tasks:
        return None

    by_room: dict[str, list[dict]] = {}
    for task in due_tasks:
        room = task.get("location_name") or "Unplaced"
        by_room.setdefault(room, []).append(task)

    lines = []
    for room, tasks in by_room.items():
        lines.append(f"  {room} ({len(tasks)}):")
        for task in tasks:
            name = task.get("nickname") or task.get("common_name") or task.get("plant_id")
            action = task.get("action", "care")
            lines.append(f"    • {name} — {action}")

    plant_ids = [t["plant_id"] for t in due_tasks]
    single_id = plant_ids[0] if len(plant_ids) == 1 else None

    return {
        "title": "Garden Morning Digest",
        "body": "Plants needing care today:\n" + "\n".join(lines),
        "actions": [
            notify.callback_action(
                "Mark Watered",
                "garden_done",
                {"plant_id": single_id, "plant_ids": plant_ids, "action": "water"},
            ),
            notify.callback_action(
                "Still damp (+2d)",
                "garden_snooze",
                {"plant_id": single_id, "plant_ids": plant_ids, "postpone_days": 2},
            ),
        ],
    }


def collect_due_tasks(
    session: Session,
    member: str | None = None,
    *,
    room_names: dict[str, str] | None = None,
) -> list[dict]:
    """Collect due/overdue care tasks, optionally filtered to one member.

    Effective assignment: ``plant.assigned_to``, else the location's
    ``assigned_to``.  ``member=None`` returns everything due.

    Rooms are Home Hub's areas (``services/garden/hub_rooms.py``), so a plant in
    one holds an area id and has no local row: ``room_names`` (``area_id ->
    name``) is what names it here. Without it such a plant reads as unplaced.
    """
    now = datetime.now(UTC)
    rules = session.exec(select(CareRule).where(CareRule.paused == False)).all()  # noqa: E712
    tasks = []
    for rule in rules:
        plant = session.get(Plant, rule.plant_id)
        if plant is None or plant.status != "active":
            continue
        location = session.get(Location, plant.location_id) if plant.location_id else None
        plant_assigned = plant.assigned_to or (location.assigned_to if location else None)
        if member is not None and plant_assigned != member:
            continue
        next_due = rule.next_due_at
        if next_due:
            try:
                due_dt = datetime.fromisoformat(next_due)
            except ValueError:
                due_dt = None
        else:
            due_dt = None
        if due_dt is None:
            continue
        if rule.snoozed_until:
            try:
                snooze_dt = datetime.fromisoformat(rule.snoozed_until)
            except ValueError:
                snooze_dt = None
            if snooze_dt and snooze_dt > now:
                continue
        if due_dt > now:
            continue
        species = None
        # Resolve species common_name lazily via the caller if needed.
        tasks.append(
            {
                "plant_id": plant.id,
                "nickname": plant.nickname,
                "common_name": plant.species_ref,
                "location_name": location.name if location else (room_names or {}).get(plant.location_id or ""),
                "location_id": plant.location_id,
                "action": rule.action,
                "next_due_at": rule.next_due_at,
                "overdue": due_dt < now,
                "assigned_to": plant_assigned,
            }
        )
    return tasks


def _digest_time_today(daily_digest_time: str | None) -> datetime | None:
    """Today's ``HH:MM`` digest time as a household-timezone datetime, or None if unset.

    Uses the household timezone from the ``TZ`` environment variable so a
    08:00 digest means 08:00 at home, not 08:00 UTC.
    """
    if not daily_digest_time:
        return None
    try:
        hh, mm = (int(p) for p in daily_digest_time.split(":"))
    except (ValueError, AttributeError):
        return None
    import os

    tz_name = os.getenv("TZ", "UTC")
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
    except Exception:
        tz = UTC
    now = datetime.now(tz)
    return now.replace(hour=hh, minute=mm, second=0, microsecond=0, tzinfo=tz)


async def send_digest_to_member(
    member: str,
    session_factory,
    *,
    client=None,
) -> bool:
    """Build and send one morning digest to a member via Telemetry.

    Returns True when telemetry accepted the notice.
    """
    from services.garden.main import _seed as _garden_seed
    from services.garden import hub_rooms as rooms_hub

    # Rooms are Home Hub's areas: name them for the digest.
    rooms = {str(a["area_id"]): str(a["name"]) for a in await rooms_hub.list_rooms() or []}

    with Session(session_factory()) as session:
        tasks = collect_due_tasks(session, member, room_names=rooms)
        seed = _garden_seed()
        # Enrich with species common names.
        for task in tasks:
            plant = session.get(Plant, task["plant_id"])
            if plant:
                species = seed.get(plant.species_ref) if seed.present else None
                if species:
                    task["common_name"] = species.get("common_name") or plant.species_ref

    digest = build_digest(member, tasks)
    if digest is None:
        return True  # nothing due, nothing to send

    day = datetime.now(UTC).date().isoformat()
    payload = notify.notice(
        user=member,
        category="garden",
        title=digest["title"],
        body=digest["body"],
        actions=digest["actions"],
        once=_once_key(member, day),
        kind="garden_digest",
        audience="all",
    )
    try:
        return await notify.send(payload, client=client)
    except Exception as e:
        log.warning("Garden digest to %s failed: %s", member, e)
        return False


async def digest_loop(engine_factory, get_family, client=None):
    """Background loop: at ``daily_digest_time`` each day, send a digest to
    every family member. Disabled under pytest."""
    import os

    if "PYTEST_CURRENT_TEST" in os.environ:
        log.info("[Garden] digest loop disabled under pytest")
        return

    while True:
        try:
            now = datetime.now(UTC)
            with Session(engine_factory()) as session:
                profile = session.exec(
                    select(GardenProfile).where(GardenProfile.user_id == "household")
                ).first()
                digest_time = profile.daily_digest_time if profile else None
            if digest_time:
                target = _digest_time_today(digest_time)
                if target and now >= target and (now - target).total_seconds() < LOOP_INTERVAL_S:
                    # Send to every family member.
                    try:
                        family = await get_family()
                    except Exception as e:
                        log.warning("[Garden] could not list family for digest: %s", e)
                        family = []
                    for member in family:
                        await send_digest_to_member(member, engine_factory, client=client)
                    # Sleep past the window to avoid re-sending.
                    await asyncio.sleep(LOOP_INTERVAL_S * 2)
        except Exception as e:
            log.warning("[Garden] digest loop error: %s", e)
        await asyncio.sleep(LOOP_INTERVAL_S)
