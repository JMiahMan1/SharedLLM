"""Calendar reminders: "Dentist at 2 pm" a set time before each event.

Every two minutes, for each person, read their own events (the same reading
and ownership rule leave-by uses, ``leave_by.events_for``) and send one
notice per event a set number of minutes before it starts. The number is the
person's ``reminder_minutes`` calendar setting (default 30; 0 turns calendar
reminders off). An all-day event has no start worth counting down to, so it
gets one morning notice at 8:00 on each of its days instead. Clock times are
the person's ``timezone`` calendar setting, else the household's. Telemetry's
``once`` key keeps each event to one reminder across rounds and restarts.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta

import pytz

from services.execution.leave_by import _clock, _start, all_day, events_for

log = logging.getLogger("execution.calendar_reminders")

INTERVAL_S = 120
DEFAULT_LEAD_MIN = 30
#: Longest lead a person may set (a day): the read window grows with it.
MAX_LEAD_MIN = 24 * 60
MORNING = time(8, 0)
#: How long after 8:00 the morning notice may still go out (a restart at
#: 9:30 still sends it; one at 3 pm does not).
MORNING_WINDOW = timedelta(hours=2)


def lead_minutes(settings: dict) -> int:
    """The person's ``reminder_minutes`` setting, clamped; default 30."""
    raw = (settings or {}).get("reminder_minutes")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LEAD_MIN
    return max(0, min(value, MAX_LEAD_MIN))


def person_tz(settings: dict):
    """The person's ``timezone`` setting when it names a real zone, else the household's."""
    from services.execution.handlers.calendar import _get_local_tz

    name = str((settings or {}).get("timezone") or "").strip()
    if name:
        try:
            return pytz.timezone(name)
        except pytz.UnknownTimeZoneError:
            log.info(f"[calendar_reminders] unknown timezone {name!r}; using the household's")
    return _get_local_tz()


def _days(event: dict) -> list:
    """The dates an all-day event covers (its end date is exclusive)."""
    start = _start(event)
    if not start:
        return []
    first = start.date()
    last = first
    raw_end = event.get("end_time")
    if raw_end:
        try:
            end = datetime.fromisoformat(str(raw_end).replace("Z", "+00:00"))
            last = max(first, end.date() - timedelta(days=1))
        except ValueError:
            pass
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _key(event: dict) -> str:
    return str(event.get("id") or event.get("summary") or "event")


def due(events: list[dict], now: datetime, lead_min: int) -> list[dict]:
    """The notices due at ``now`` (aware, in the person's zone)."""
    out = []
    morning = now.replace(hour=MORNING.hour, minute=MORNING.minute, second=0, microsecond=0)
    for ev in events:
        start = _start(ev)
        if not start:
            continue
        title = ev.get("summary") or "Event"
        where = (ev.get("location") or "").strip()
        base = {
            "category": "calendar",
            "kind": "calendar",
            "priority": 3,
            "tag": f"calendar:{_key(ev)}"[:120],
            "data": {"url": "/calendar", "start_time": ev["start_time"]},
        }
        if all_day(ev):
            today = now.date()
            if today in _days(ev) and morning <= now < morning + MORNING_WINDOW:
                out.append({
                    **base,
                    "title": f"Today: {title}"[:120],
                    "body": f"All day{f' at {where}' if where else ''}.",
                    "once": f"calendar:{_key(ev)}:{today.isoformat()}"[:200],
                })
            continue
        left = (start - now).total_seconds()
        if lead_min and 0 < left <= lead_min * 60:
            mins = max(1, round(left / 60))
            soon = f"In {mins} min" if mins < 60 else f"In {mins // 60} h {mins % 60:02d} min"
            out.append({
                **base,
                "title": f"{title} at {_clock(start.timestamp(), now.tzinfo)}"[:120],
                "body": f"{soon}{f' at {where}' if where else ''}.",
                "once": f"calendar:{_key(ev)}:{ev['start_time']}"[:200],
            })
    return out


async def remind_once(now_utc: datetime | None = None) -> int:
    """One round over everyone; returns how many reminders were sent."""
    from services.common import notify
    from services.execution.main import resolve_internal_user

    now_utc = now_utc or datetime.now(pytz.utc)
    sent = 0
    for user in await notify.family():
        creds = await resolve_internal_user(rag_user=user)
        if not creds:
            continue
        settings = creds.get("calendar_settings") or {}
        lead = lead_minutes(settings)
        now = now_utc.astimezone(person_tz(settings))
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        until = max(now + timedelta(minutes=lead, seconds=INTERVAL_S), day_start + timedelta(days=1))
        try:
            events = await events_for(user, creds, day_start.isoformat(), until.isoformat())
        except Exception as e:
            log.info(f"[calendar_reminders] no calendar for {user}: {e}")
            continue
        for note in due(events, now, lead):
            if await notify.send({"user": user, **note}):
                sent += 1
                log.info(f"[calendar_reminders] {user}: {note['title']}")
    return sent


async def calendar_reminder_loop() -> None:
    while True:
        await asyncio.sleep(INTERVAL_S)
        try:
            await remind_once()
        except Exception as e:  # keep looping, but never fail silently
            log.warning(f"[calendar_reminders] pass failed: {type(e).__name__}: {e}")
