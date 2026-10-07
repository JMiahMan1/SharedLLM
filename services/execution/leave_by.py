"""Leave-by reminders: "Leave by 2:40 for Dentist -- 22 min drive".

Every few minutes, for each person with a recent position, read their
calendars for events starting in the next few hours that have a location, ask
geo for the drive time there by road from where they are now (OSRM; the
location is a zone name, an address or a place name), and once the time to
leave is near, send one notice through telemetry. Telemetry's ``once`` key
keeps each event to a single reminder across rounds and restarts.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime

import aiohttp

log = logging.getLogger("execution.leave_by")

INTERVAL_S = 300
#: Events starting further out than this are not looked at yet.
HORIZON_S = 4 * 3600
#: Time to park and walk in, added to the drive.
BUFFER_S = 10 * 60
#: Remind this long before the time to leave (one round of slack included).
LEAD_S = 10 * 60
#: A position older than this is not "where they are leaving from".
FRESH_S = 1800


def _start(event: dict) -> datetime | None:
    raw = event.get("start_time")
    if not raw:
        return None
    try:
        start = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return start if start.tzinfo else None


def belongs_to(event: dict, user: str, creds: dict) -> bool:
    """Whether the event is this person's to drive to.

    A household calendar (Skylight) is shared: everyone reads everyone's
    events, so "Work at Discount Tire" is not a reason to remind the whole
    family. Theirs is: a Skylight event filed under their name, an event on a
    calendar account mapped to them in calendar settings ("people"), or one
    from their own Nextcloud or iCal calendars. Household-wide events are left
    out -- who drives is not knowable.
    """
    names = {user.lower()}
    names.update(str(n).strip().lower() for n in (creds.get("display_name"),) if n)
    integration = event.get("integration")
    if integration == "ical":
        return True  # this person's own iCal subscriptions
    if integration == "nextcloud":
        return (creds.get("credential_sources") or {}).get("nextcloud") == "own"
    person = str(event.get("person") or "").strip().lower()
    if person and (person in names or person.split()[0] in names):
        return True
    calendar = event.get("calendar")
    for p in ((creds.get("calendar_settings") or {}).get("people") or []):
        if str(p.get("name") or "").strip().lower() in names and calendar and calendar in (p.get("accounts") or []):
            return True
    return False


def destination(event: dict) -> str:
    """Where to route to: the calendar's own coordinates when it has them,
    else the location text (geo geocodes it)."""
    if event.get("lat") is not None and event.get("lon") is not None:
        return f"{float(event['lat']):.6f},{float(event['lon']):.6f}"
    return event["location"].strip()


def candidates(events: list[dict], now: float) -> list[dict]:
    """Events with a location starting within the horizon, not all-day."""
    out = []
    for ev in events:
        start = _start(ev)
        where = (ev.get("location") or "").strip()
        if not start or not where or ev.get("all_day"):
            continue
        # All-day events from calendars without the flag come through as local midnight
        if (start.hour, start.minute, start.second) == (0, 0, 0):
            continue
        if 0 < start.timestamp() - now <= HORIZON_S:
            out.append(ev)
    return out


def leave_at(event: dict, eta: dict) -> float | None:
    """When to leave for the event, or None when there or already driving."""
    if eta.get("arrived") or eta.get("moving"):
        return None  # there already, or on the way
    start = _start(event)
    return start.timestamp() - float(eta.get("duration_s") or 0) - BUFFER_S if start else None


def _clock(ts: float, tz) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%-I:%M %p").replace(":00 ", " ").lower()


def reminder(event: dict, eta: dict, leave_ts: float, now: float, tz) -> dict:
    mins = max(1, round(float(eta.get("duration_s") or 0) / 60))
    drive = f"{mins} min" if mins < 60 else f"{mins // 60} h {mins % 60:02d} min"
    title = event.get("summary") or "your event"
    head = "Leave now" if leave_ts <= now else f"Leave by {_clock(leave_ts, tz)}"
    start = _start(event)
    return {
        "kind": "leave_by",
        "title": f"{head} for {title}"[:120],
        "body": f"{drive} drive to {event['location']}; starts at {_clock(start.timestamp(), tz)}.",
        "data": {"location": event["location"], "start_time": event["start_time"], "duration_s": eta.get("duration_s")},
        "once": f"leave_by:{event.get('id') or ''}:{event['start_time']}:{title}"[:200],
    }


async def _json(session, method: str, url: str, **kw):
    async with session.request(method, url, timeout=aiohttp.ClientTimeout(total=15), **kw) as resp:
        return resp.status, await resp.json(content_type=None)


async def _events_for(user: str) -> list[dict]:
    """This person's own upcoming events (see belongs_to)."""
    from services.execution.handlers.calendar import handle_calendar
    from services.execution.main import resolve_internal_user
    from services.execution.schemas import CalendarRequest, UserContext

    creds = await resolve_internal_user(rag_user=user)
    if not creds:
        return []
    result = await handle_calendar(CalendarRequest(user_context=UserContext(**creds), action="read"))
    return [ev for ev in (getattr(result, "events", None) or []) if belongs_to(ev, user, creds)]


async def remind_once() -> int:
    """One round over everyone; returns how many reminders were sent."""
    from services.config import GEO_SVC_URL, IDENTITY_SVC_URL, INTERNAL_SECRET, TELEMETRY_SVC_URL
    from services.execution.handlers.calendar import _get_local_tz

    hdr = {"X-Internal-Secret": INTERNAL_SECRET}
    tz = _get_local_tz()
    sent = 0
    async with aiohttp.ClientSession() as session:
        status, locations = await _json(session, "GET", f"{IDENTITY_SVC_URL.rstrip('/')}/api/users/location/all", headers=hdr)
        if status != 200 or not isinstance(locations, dict):
            return 0
        for user, loc in sorted(locations.items()):
            now = time.time()
            stamp = float((loc or {}).get("updated_at") or (loc or {}).get("timestamp") or 0) if isinstance(loc, dict) else 0
            if now - stamp > FRESH_S:
                continue
            try:
                events = candidates(await _events_for(user), now)
            except Exception as e:
                log.info(f"[leave_by] no calendar for {user}: {e}")
                continue
            for ev in events:
                status, eta = await _json(session, "GET", f"{GEO_SVC_URL.rstrip('/')}/people/{user}/eta", headers=hdr,
                                          params={"to": destination(ev), "viewer": user, "is_admin": "false"})
                if status != 200 or not isinstance(eta, dict):
                    continue  # place not found, or no drive time right now
                leave_ts = leave_at(ev, eta)
                if leave_ts is None or now < leave_ts - LEAD_S:
                    continue
                note = reminder(ev, eta, leave_ts, now, tz)
                status, body = await _json(session, "POST", f"{TELEMETRY_SVC_URL.rstrip('/')}/api/telemetry/notify",
                                           headers=hdr, json={"user": user, **note})
                if status == 200 and not (body or {}).get("duplicate"):
                    sent += 1
                    log.info(f"[leave_by] {user}: {note['title']}")
    return sent


async def leave_by_loop() -> None:
    while True:
        await asyncio.sleep(INTERVAL_S)
        with contextlib.suppress(Exception):
            await remind_once()
