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
from datetime import date, datetime, timedelta

import aiohttp
from sqlmodel import Session, select

from services.common import notify
from services.common.notice_links import link

from . import books as book_table
from . import plans
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
# Reengagement: fires at 10:00 for readers who have not read recently.
REENGAGE_HOUR = 10
# Steps at which a nudge is sent: 3 days, 7, 14, 30, then every 30 after that.
REENGAGE_STEPS_DAYS = (3, 7, 14, 30)


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


def _chapter_link(book: str, chapter: int, verse: int | None, translation: str | None = None) -> dict:
    """The reader on the whole chapter, scrolled to and highlighting ``verse``.

    The Bible page reads ``ref`` for the passage (``book`` alone means a study
    book there), so a chapter link is ``ref=John 3:16`` plus ``verse`` and the
    ``#v16`` anchor; the reader always shows the whole chapter around it.
    """
    verse = verse or 1
    return link("bible", tab="read", ref=f"{book} {chapter}:{verse}", verse=verse,
                translation=translation or None, hash=f"v{verse}")


def _build_deep_link(state: UserBibleState) -> dict:
    """Where the reader left off: a study section, else the chapter and verse."""
    if state.last_study_book_id:
        return _build_study_link(state)
    if not state.last_book or not state.last_chapter:
        return link("bible", tab="read")
    return _chapter_link(state.last_book, state.last_chapter, state.last_verse, state.default_version)


def _build_study_link(state: UserBibleState) -> dict:
    """The Studies tab at the study book and section the reader last had open."""
    if not state.last_study_book_id:
        return link("bible", tab="read")
    ordinal = state.last_study_section_ordinal or None
    return link("bible", tab="studies", book=state.last_study_book_id, ordinal=ordinal)


def _build_plan_link(plan_id: str, plan_day: int) -> dict:
    """Build a deep link to a plan day's first reading chapter."""
    readings = plans.day_readings(plan_id, plan_day)
    if not readings:
        return link("bible", tab="today")
    first = readings[0]
    return _chapter_link(first["osis"], first["chapter_start"], 1)


def _build_memorize_link() -> dict:
    """Build a deep link to the memorize tab."""
    return link("bible", tab="memorize")


def message_for(session: Session, username: str, today: str) -> dict:
    """What to say: the plan reading first, then memory verses, then a nudge.

    Includes a deep link to the exact chapter and verse the reader left off at.
    """
    state = session.get(UserBibleState, username)
    deep_link = _build_deep_link(state) if state else link("bible", tab="read")
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
                "data": {"link": plan_link, "url": plan_link["href"]},
            }
    due = sum(
        1
        for r in session.exec(select(MemoryVerse).where(MemoryVerse.username == username)).all()
        if r.due_on <= today
    )
    if due:
        memorize_link = _build_memorize_link()
        return {
            "title": "Memory verses to practise",
            "body": f"{due} verse{'s' if due != 1 else ''} ready for review.",
            "data": {"link": memorize_link, "url": memorize_link["href"]},
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
        "data": {"link": deep_link, "url": deep_link["href"]},
    }


def streak_message_for(session: Session, username: str, today: str) -> dict | None:
    """A gentle 'keep your streak' message for readers with an active streak.

    Returns None if no streak or if the reader already read today.
    Includes the last reading position for a personal touch.
    """
    from . import metrics as metrics_mod

    picture = metrics_mod.compute(session, username)
    streak = picture.get("read_streak_current", 0)
    if streak == 0:
        return None
    if read_today(session, username, today):
        return None
    state = session.get(UserBibleState, username)
    deep_link = _build_deep_link(state) if state else link("bible", tab="read")
    verse = pick_daily_encouragement()
    # Vary the wording based on streak length
    if streak == 1:
        body = "Day 1 down! Keep going: your streak is just beginning."
    elif streak < 7:
        body = f"You're on a {streak}-day streak! Don't break it now."
    elif streak < 30:
        body = f"A {streak}-day streak: that's faithfulness. Keep it going."
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
    body += f" {verse.text}: {verse.reference}"
    return {
        "title": "Keep your streak going",
        "body": body,
        "data": {"link": deep_link, "url": deep_link["href"]},
    }


def reengage_steps() -> list[int]:
    """All step values, including the repeating 30-day tail."""
    out = list(REENGAGE_STEPS_DAYS)
    next_step = out[-1] + 30
    while next_step < 1000:
        out.append(next_step)
        next_step += 30
    return out


def reengage_due(last_read_day: str | None, last_sent_day: str, today: str) -> bool:
    """Whether a reengagement nudge should go out today.

    One nudge per step: 3, 7, 14, 30 days after the last reading, then every
    30 days. A reader who never opened the app is treated as reaching the
    first step (3) on the first check.
    """
    today_dt = date.fromisoformat(today)
    if last_read_day:
        try:
            last = date.fromisoformat(last_read_day)
        except ValueError:
            last = today_dt
        days_since = (today_dt - last).days
    else:
        # Never read: first check is treated as day 3 reached.
        days_since = 3
    steps = reengage_steps()
    # The step reached today: the largest step not exceeding days_since.
    current_step = None
    for s in steps:
        if s <= days_since:
            current_step = s
        else:
            break
    if current_step is None:
        return False
    # The date on which this step was first reached.
    if last_read_day:
        try:
            step_reached = last + timedelta(days=current_step)
        except Exception:
            step_reached = today_dt
    else:
        # First check: the step was "reached" today.
        step_reached = today_dt
    # Send only if we have not already nudged on or after the step-reached date.
    if last_sent_day:
        try:
            sent = date.fromisoformat(last_sent_day)
        except ValueError:
            sent = date.min
        if sent >= step_reached:
            return False
    return True


def reengage_message(days: int | None) -> dict:
    """A warm reengagement message with a link back to the reader."""
    bodies = [
        "Today's verse is ready whenever you are.",
        "Try a reading plan: a few minutes a day.",
        "Listen to a chapter read aloud on the way somewhere.",
        "Play today's Bible quiz with the family.",
        "Memorize one verse this week.",
        "Pick up where you left off.",
    ]
    body = bodies[days % len(bodies)] if days is not None else bodies[0]
    verse = pick_daily_encouragement()
    body += f" {verse.text}: {verse.reference}"
    deep_link = link("bible", tab="read")
    # Same shape as message_for: telemetry's notify takes the link inside data (a top-level
    # "link" key is refused with HTTP 422).
    return {
        "title": "Bible",
        "body": body,
        "data": {"link": deep_link, "url": deep_link["href"]},
    }


async def _send_reengage(username: str, today: str, telemetry_url: str, secret: str, note: dict) -> bool:
    """Send a reengagement notice (title, body, link from ``reengage_message``) with its own once key."""
    body = {
        "user": username,
        "category": "bible",
        "kind": "bible_reengage",
        "tag": "bible:reengage",
        "once": f"bible-reengage:{today}",
        **note,
    }
    return await notify.send(body, telemetry=telemetry_url, secret=secret)


async def reengage_once(engine, telemetry_url: str, secret: str, identity_url: str, now: datetime | None = None) -> int:
    """One pass over the household for reengagement nudges.

    Fires only when ``now.hour == REENGAGE_HOUR`` and we are outside quiet
    hours. Identity unreachable means nothing is sent.
    """
    now = now or datetime.now()
    if now.hour != REENGAGE_HOUR or _in_quiet_hours(now.hour):
        return 0
    today = now.date().isoformat()
    if not identity_url:
        return 0
    try:
        async with aiohttp.ClientSession() as client, client.get(
            f"{identity_url.rstrip('/')}/api/internal/household",
            headers={"X-Internal-Secret": secret},
            timeout=aiohttp.ClientTimeout(total=10.0),
        ) as resp:
            if resp.status >= 400:
                log.warning("[Bible] household lookup failed: HTTP %s", resp.status)
                return 0
            data = await resp.json()
    except Exception as exc:
        log.warning("[Bible] household lookup errored: %s", exc)
        return 0
    usernames = data.get("usernames") if isinstance(data, dict) else None
    if not isinstance(usernames, list):
        log.warning("[Bible] household lookup returned an unexpected shape")
        return 0
    reading_kinds = READING_KINDS - OPEN_KINDS
    sent = 0
    with Session(engine) as session:
        for username in usernames:
            state = session.get(UserBibleState, username)
            # No row yet means defaults: reengage on, no reminder time, never nudged.
            if state is not None and not state.reengage:
                continue
            if state is not None and state.reminder_time:
                continue
            if read_today(session, username, today):
                continue
            # Newest reading-day event (not an open).
            last_read_day = None
            for evt in session.exec(
                select(ReadingEvent)
                .where(ReadingEvent.username == username, ReadingEvent.kind.in_(reading_kinds))
                .order_by(ReadingEvent.day.desc())
            ).all():
                if evt.day:
                    last_read_day = evt.day
                    break
            last_sent_day = state.reengage_last_sent if state is not None else ""
            if not reengage_due(last_read_day, last_sent_day, today):
                continue
            try:
                days = (date.fromisoformat(today) - date.fromisoformat(last_read_day)).days if last_read_day else None
                ok = await _send_reengage(username, today, telemetry_url, secret, reengage_message(days))
                if ok:
                    sent += 1
                    if state is None:
                        state = UserBibleState(username=username, reengage_last_sent=today)
                        session.add(state)
                    else:
                        state.reengage_last_sent = today
                    session.commit()
            except Exception as exc:
                log.warning("[Bible] reengage for %s failed: %s", username, exc)
    return sent


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


async def reminder_loop(
    engine_factory, telemetry_url: str, secret: str, identity_url: str | None = None
) -> None:
    if not telemetry_url:
        log.warning("[Bible] TELEMETRY_SVC_URL is not resolved; reading reminders are off")
        return
    while True:
        await asyncio.sleep(INTERVAL_S)
        now = datetime.now()
        try:
            await remind_once(engine_factory(), telemetry_url, secret, now)
        except Exception as e:  # keep looping, but never fail silently
            log.warning(f"[Bible] reminder pass failed: {type(e).__name__}: {e}")
        try:
            await reengage_once(engine_factory(), telemetry_url, secret, identity_url, now)
        except Exception as e:  # keep looping, but never fail silently
            log.warning(f"[Bible] reengage pass failed: {type(e).__name__}: {e}")
