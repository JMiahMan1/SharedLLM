# services/bible/reminders.py
"""The daily reading reminder: one gentle push if today has no reading yet.

A reader picks a time (``UserBibleState.reminder_time``, "HH:MM" in the
server's local time, the same clock reading days are counted on; empty means
off). A loop inside the bible service checks every few minutes, sends only
within two hours after that time (never hours late), the pattern
execution's leave-by notices use, and sends through telemetry's notify with a
``once`` key per reader per day, so a restart or a second replica never sends
it twice. The message points at what is actually waiting: today's plan
reading, the memory verses due, or simply the streak.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlmodel import Session, select

from services.common import notify

from . import plans
from .metrics import OPEN_KINDS, READING_KINDS
from .models import MemoryVerse, PlanEnrollment, ReadingEvent, UserBibleState

log = logging.getLogger(__name__)

INTERVAL_S = 300
# How long after the chosen time a nudge may still go out. Without a window a
# reminder set for 07:00 at nine at night (or a server back up late in the
# evening) would push "time to read" at once, hours after it means anything.
WINDOW = timedelta(hours=2)


def read_today(session: Session, username: str, today: str) -> bool:
    """Whether the reader did any actual reading today.

    Opening the app or starting a plan is not reading, so neither silences the
    reminder.
    """
    kinds = READING_KINDS - OPEN_KINDS - {"plan_started"}
    return (
        session.exec(
            select(ReadingEvent.id).where(
                ReadingEvent.username == username, ReadingEvent.day == today, ReadingEvent.kind.in_(kinds)
            )
        ).first()
        is not None
    )


def message_for(session: Session, username: str, today: str) -> dict:
    """What to say: the plan reading first, then memory verses, then a nudge."""
    enrolled = session.exec(
        select(PlanEnrollment).where(PlanEnrollment.username == username, PlanEnrollment.active == True)  # noqa: E712
    ).all()
    for row in enrolled:
        done = [int(d) for d in row.completed_days.split(",") if d.strip().isdigit()]
        state = plans.progress(row.plan_id, row.started_on, done)
        day = state["current_day"]
        if day and not state["finished"]:
            refs = ", ".join(r["ref"] for r in plans.day_readings(row.plan_id, day))
            return {
                "title": f"Today in {plans.find(row.plan_id)['name']}",
                "body": f"Day {day}: {refs}",
                "data": {"url": f"/bible?ref={quote(refs.split(',')[0].strip())}"},
            }
    due = sum(
        1
        for r in session.exec(select(MemoryVerse).where(MemoryVerse.username == username)).all()
        if r.due_on <= today
    )
    if due:
        return {
            "title": "Memory verses to practise",
            "body": f"{due} verse{'s' if due != 1 else ''} ready for review.",
            "data": {"url": "/bible?tab=memorize"},
        }
    return {
        "title": "A few minutes in the Word?",
        "body": "Pick up where you left off.",
        "data": {"url": "/bible"},
    }


def due_now(session: Session, now: datetime) -> list[tuple[str, dict]]:
    """Readers whose reminder time passed within ``WINDOW`` and who have not read today."""
    today = now.date().isoformat()
    out = []
    for state in session.exec(select(UserBibleState).where(UserBibleState.reminder_time != "")).all():
        try:
            hour, minute = (int(part) for part in state.reminder_time.split(":"))
        except ValueError:
            continue
        due_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if not (due_at <= now < due_at + WINDOW) or read_today(session, state.username, today):
            continue
        out.append((state.username, message_for(session, state.username, today)))
    return out


async def send(username: str, note: dict, today: str, telemetry_url: str, secret: str) -> bool:
    body = {"user": username, "category": "bible", "kind": "bible_reminder", "tag": "bible:reminder",
            "once": f"bible-nudge:{today}", **note}
    return await notify.send(body, telemetry=telemetry_url, secret=secret)


async def remind_once(engine, telemetry_url: str, secret: str, now: datetime | None = None) -> int:
    now = now or datetime.now()
    with Session(engine) as session:
        pending = due_now(session, now)
    sent = 0
    for username, note in pending:
        try:
            sent += await send(username, note, now.date().isoformat(), telemetry_url, secret)
        except Exception as exc:
            log.warning("[Bible] reminder for %s failed: %s", username, exc)
    return sent


async def reminder_loop(engine_factory, telemetry_url: str, secret: str) -> None:
    if not telemetry_url:
        log.warning("[Bible] TELEMETRY_SVC_URL is not resolved; reading reminders are off")
        return
    while True:
        await asyncio.sleep(INTERVAL_S)
        try:
            await remind_once(engine_factory(), telemetry_url, secret)
        except Exception as e:  # keep looping, but never fail silently
            log.warning(f"[Bible] reminder pass failed: {type(e).__name__}: {e}")
