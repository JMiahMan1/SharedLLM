"""Place reminders: "remind me when I get home".

A person sets a reminder on one of the family's places (a Home Assistant
zone) for when they arrive or leave. Geo already turns every phone fix into
an arrival or departure (``_presence_transition`` in main), so a reminder
needs no geofence of its own on the phone: the fix the app posts anyway is
the trigger. When the person's own arrival or departure matches, they get one
``timers`` notice (the category timed reminders ring under) and the reminder
is gone, like a timed reminder that went off.

Reminders are personal: only the person who set one hears it, and only their
own movements fire it. The functions here only read and write Redis; main
wires them to the zone transitions and the HTTP routes.
"""
from __future__ import annotations

import json
import re
import time
import uuid

from services.common.links import app_link  # the timers' rule for in-app links

#: Hash per person: reminder id -> {"id", "zone", "zone_name", "on", "title", "url", "created_at"}.
KEY = "geo:place_reminders:{user}"
#: At most this many waiting reminders per person.
MAX_PER_USER = 50
TITLE_MAX = 200
EVENTS = {"arrive": "arrived", "leave": "departed"}
_ZONE_ID = re.compile(r"^zone\.[a-z0-9_]+$")
_REMINDER_ID = re.compile(r"^[0-9a-f]{32}$")


def _decode(value):
    return value.decode() if isinstance(value, bytes) else value


def validate(body) -> dict:
    """The reminder to store from a request body, or ValueError with the reason."""
    if not isinstance(body, dict):
        raise ValueError("The body must be an object")
    zone = body.get("zone")
    if not isinstance(zone, str) or not _ZONE_ID.match(zone):
        raise ValueError("zone must be a place's zone id")
    on = body.get("on")
    if on not in EVENTS:
        raise ValueError("on must be arrive or leave")
    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("title is required")
    url = body.get("url")
    link = app_link(url)
    if url not in (None, "") and link is None:
        raise ValueError("url must be a path inside the app")
    return {"zone": zone, "on": on, "title": title.strip()[:TITLE_MAX], "url": link}


async def list_for(r, user: str) -> list[dict]:
    """The person's waiting reminders, oldest first."""
    raw = await r.hgetall(KEY.format(user=user)) or {}
    out = []
    for value in raw.values():
        try:
            entry = json.loads(_decode(value))
        except (TypeError, ValueError):
            continue
        if isinstance(entry, dict) and entry.get("id"):
            out.append(entry)
    return sorted(out, key=lambda e: e.get("created_at", 0))


async def add(r, user: str, reminder: dict, zone_name: str) -> dict:
    """Store a validated reminder; ValueError when the person has too many."""
    if len(await list_for(r, user)) >= MAX_PER_USER:
        raise ValueError(f"You already have {MAX_PER_USER} place reminders waiting")
    entry = {
        "id": uuid.uuid4().hex,
        **reminder,
        "zone_name": zone_name,
        "created_at": time.time(),
    }
    await r.hset(KEY.format(user=user), entry["id"], json.dumps(entry))
    return entry


async def remove(r, user: str, reminder_id: str) -> bool:
    """Forget one reminder; False when it is not (or no longer) waiting."""
    if not _REMINDER_ID.match(reminder_id or ""):
        return False
    key = KEY.format(user=user)
    if await r.hget(key, reminder_id) is None:
        return False
    await r.hdel(key, reminder_id)
    return True


def notice_text(entry: dict) -> tuple[str, str]:
    """The notice's title and body for a reminder that went off."""
    where = entry.get("zone_name") or entry.get("zone", "")
    verb = "Arrived at" if entry.get("on") == "arrive" else "Left"
    return f"📍 Reminder: {entry.get('title', '')}", f"{verb} {where}"


async def due(r, user: str, kind: str, zone_id: str) -> list[dict]:
    """Take the person's reminders that this arrival or departure fires.

    Each is removed before it is returned, so one movement rings it once even
    when fixes race each other: ``hdel`` answering 0 means another fix
    already took it."""
    fired = []
    key = KEY.format(user=user)
    for entry in await list_for(r, user):
        if entry.get("zone") != zone_id or EVENTS.get(entry.get("on")) != kind:
            continue
        if await r.hdel(key, entry["id"]):
            fired.append(entry)
    return fired
