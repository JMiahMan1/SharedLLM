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

A separate "keep your streak" reminder fires if the reader has a streak and
hasn't read by evening, at most once per day, never shaming, easy to turn off,
and respecting quiet hours.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlmodel import Session, select

from services.common import notify

from . import plans
from . import books as book_table
from .encouragement import pick_daily_encouragement
from .metrics import OPEN_KINDS, READING_KINDS
from .models import MemoryVerse, PlanEnrollment, PlanGroup, PlanGroupMember, ReadingEvent, UserBibleState

log = logging.getLogger(__name__)

INTERVAL_S = 300
# How long after the chosen time a nudge may still go out. Without a window a
# reminder set for 07:00 at nine at night (or a server back up late in the
# evening) would push "time to read" at once, hours after it means anything.
WINDOW = timedelta(hours=2)
# Quiet hours: no reminders between 22:00 and 07:00 local time
QUIET_START = 22
QUIET_END = 7
# Streak reminder: fires at 19:00 if streak > 0 and no reading today
STREAK_REMINDER_HOUR = 19


def _in_quiet_hours(hour: int) -> bool:
    """Check if an hour falls within quiet hours (22:00-07:00)."""
    return hour >= QUIET_START or hour < QUIET_END


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


def _build_deep_link(state: UserBibleState) -> str:
    """Build a deep link to the reader's last position: whole chapter with verse highlighted."""
    # Study takes priority if that's what they were last reading
    if state.last_study_book_id:
        return _build_study_link(state)
    if not state.last_book:
        return "/bible"
    # Format: /bible/<book>/<chapter>?verse=<verse>#v<verse>
    # The reader route should accept this and scroll to the verse
    book = state.last_book
    chapter = state.last_chapter
    verse = state.last_verse
    return f"/bible/{book}/{chapter}?verse={verse}#v{verse}"


def _build_study_link(state: UserBibleState) -> str:
    """Build a deep link to the reader's last study position."""
    if not state.last_study_book_id:
        return "/bible"
    # Format: /bible/study/{book_id}/{ordinal}?scroll={fraction}
    # The study reader should accept this and scroll to the position
    book_id = state.last_study_book_id
    ordinal = state.last_study_section_ordinal
    fraction = state.last_study_scroll_fraction
    return f"/bible/study/{book_id}/{ordinal}?scroll={fraction}"


def _build_plan_link(plan_id: str, plan_day: int) -> str:
    """Build a deep link to a plan day's reading."""
    return f"/bible?tab=plans&plan={plan_id}&day={plan_day}"


def message_for(session: Session, username: str, today: str) -> dict:
    """What to say: the plan reading first, then memory verses, then a nudge.

    Includes a deep link to the exact chapter and verse the reader left off at.
    """
    state = session.get(UserBibleState, username)
    deep_link = _build_deep_link(state) if state else "/bible"
    enrolled = session.exec(
        select(PlanEnrollment).where(PlanEnrollment.username == username, PlanEnrollment.active == True)  # noqa: E712
    ).all()
    for row in enrolled:
        done = [int(d) for d in row.completed_days.split(",") if d.strip().isdigit()]
        plan_state = plans.progress(row.plan_id, row.started_on, done)
        day = plan_state["current_day"]
        if day and not plan_state["finished"]:
            refs = ", ".join(r["ref"] for r in plans.day_readings(row.plan_id, day))
            body = f"Day {day}: {refs}"
            plan_link = _build_plan_link(row.plan_id, day)
            if row.group_id:
                try:
                    member_of = session.exec(
                        select(PlanGroupMember).where(
                            PlanGroupMember.group_id == row.group_id,
                            PlanGroupMember.status == "joined",
                        )
                    ).all()
                    group = session.get(PlanGroup, row.group_id)
                    if group is not None and member_of:
                        read = sum(1 for m in member_of if read_today(session, m.username, today))
                        label = group.name or plans.find(row.plan_id)["name"]
                        body += f" · {read} of {len(member_of)} in {label} have read today"
                except Exception as exc:
                    log.warning("[Bible] group line for %s failed: %s", username, exc)
            return {
                "title": f"Today in {plans.find(row.plan_id)['name']}",
                "body": body,
                "data": {"url": plan_link, "link": plan_link},
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
            "data": {"url": "/bible?tab=memorize", "link": "/bible?tab=memorize"},
        }
    # Use the reader's last position for a personal nudge
    if state and state.last_study_book_id:
        # They were last reading a study
        try:
            # We don't have the book title here easily, but we can use the link
            body = "Continue your study where you left off?"
        except Exception:
            body = "Continue your study?"
    elif state and state.last_book:
        try:
            book_name = book_table.book(state.last_book)["name"]
            body = f"Pick up {book_name} {state.last_chapter} where you left off (verse {state.last_verse})?"
        except Exception:
            body = "Pick up where you left off."
    else:
        body = "A few minutes in the Word?"
    return {
        "title": "Time to read?",
        "body": body,
        "data": {"url": deep_link, "link": deep_link},
    }


def streak_message_for(session: Session, username: str, today: str) -> dict | None:
    """A gentle 'keep your streak' message for readers with an active streak.

    Returns None if no streak or if the reader already read today.
    Includes the last reading position for a personal touch.
    """
    from . import metrics as metrics_mod
    from .encouragement import pick_daily_encouragement

    picture = metrics_mod.compute(session, username)
    streak = picture.get("read_streak_current", 0)
    if streak == 0:
        return None
    if read_today(session, username, today):
        return None
    state = session.get(UserBibleState, username)
    deep_link = _build_deep_link(state) if state else "/bible"
    verse = pick_daily_encouragement()
    # Vary the wording based on streak length
    if streak == 1:
        body = "Day 1 down! Keep going — your streak is just beginning."
    elif streak < 7:
        body = f"You're on a {streak}-day streak! Don't break it now."
    elif streak < 30:
        body = f"A {streak}-day streak — that's faithfulness. Keep it going."
    else:
        body = f"{streak} days straight. You're building a habit that lasts."
    # Add the last reading position if available
    if state and state.last_study_book_id:
        body += " Continue your study where you left off?"
    elif state and state.last_book:
        try:
            book_name = book_table.book(state.last_book)["name"]
            body += f" Continue in {book_name} {state.last_chapter}:{state.last_verse}?"
        except Exception:
            body += " Want to continue where you left off?"
    body += f" {verse.text} — {verse.reference}"
    return {
        "title": "Keep your streak going",
        "body": body,
        "data": {"url": deep_link, "link": deep_link},
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
        # Respect quiet hours
        if _in_quiet_hours(hour):
            continue
        if not (due_at <= now < due_at + WINDOW) or read_today(session, state.username, today):
            continue
        out.append((state.username, message_for(session, state.username, today)))
    return out


def streak_due_now(session: Session, now: datetime) -> list[tuple[str, dict]]:
    """Readers with a streak who haven't read by evening (STREAK_REMINDER_HOUR)."""
    if now.hour != STREAK_REMINDER_HOUR:
        return []
    if _in_quiet_hours(now.hour):
        return []
    today = now.date().isoformat()
    out = []
    for state in session.exec(select(UserBibleState).where(UserBibleState.reminder_time != "")).all():
        # Only send streak reminder if they have a reminder set (engaged readers)
        if not state.reminder_time:
            continue
        if read_today(session, state.username, today):
            continue
        msg = streak_message_for(session, state.username, today)
        if msg:
            out.append((state.username, msg))
    return out


async def send(username: str, note: dict, today: str, telemetry_url: str, secret: str) -> bool:
    body = {"user": username, "category": "bible", "kind": "bible_reminder", "tag": "bible:reminder",
            "once": f"bible-nudge:{today}", **note}
    return await notify.send(body, telemetry=telemetry_url, secret=secret)


async def remind_once(engine, telemetry_url: str, secret: str, now: datetime | None = None) -> int:
    now = now or datetime.now()
    with Session(engine) as session:
        pending = due_now(session, now)
        streak_pending = streak_due_now(session, now)
    sent = 0
    for username, note in pending:
        try:
            sent += await send(username, note, now.date().isoformat(), telemetry_url, secret)
        except Exception as exc:
            log.warning("[Bible] reminder for %s failed: %s", username, exc)
    for username, note in streak_pending:
        try:
            sent += await send(username, note, now.date().isoformat(), telemetry_url, secret)
        except Exception as exc:
            log.warning("[Bible] streak reminder for %s failed: %s", username, exc)
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
