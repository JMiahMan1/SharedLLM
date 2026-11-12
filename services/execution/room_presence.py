"""Which room a person is in, from the hub's Bluetooth room detection.

The hub (services/homehub) hears each phone's beacon and publishes, per phone,
``sensor.<phone>_room`` (state: an area name, ``not_home`` or ``unknown``;
attributes ``area_id``, ``last_seen``, ``source: "ble"`` ...) and gives every
``person.*`` the live attributes ``room`` and ``room_area_id``. This module
reads those states and nothing else: it never estimates a room itself.

What it answers is deliberately narrow:

* ``known``: a room, heard recently enough (``ROOM_PRESENCE_MAX_AGE_S``).
* ``stale``: a room, but last heard too long ago to say they are still there.
* ``unknown`` / ``away``: the hub says it cannot tell, or the phone is not home.
* ``not_set_up``: the person's phones carry no beacon (no room sensor).
* ``no_person``: the hub has no ``person.*`` for that name.

Callers say so for every status but ``known``; nothing here guesses.

Who may see it follows the gateway's rule for a member's collected data
(identity's ``may_view_raw_data``): a child's room is their parents' and an
admin's, the father and mother see each other's, any other adult's is their
own. Everyone sees their own.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import aiohttp

log = logging.getLogger("execution.room_presence")

#: A room heard longer ago than this is "stale", not where the person is.
ROOM_PRESENCE_MAX_AGE_S = float(os.getenv("ROOM_PRESENCE_MAX_AGE_S", "600"))

#: The prefix a target uses to mean "the room <person> is in" (``room_of:alex``).
ROOM_OF_PREFIX = "room_of:"

_NO_ROOM = {"", "unknown", "unavailable", "none"}


class RoomTargetError(LookupError):
    """A "room <person> is in" target that cannot be resolved; the message says why."""

    def __init__(self, message: str, presence: RoomPresence | None = None) -> None:
        super().__init__(message)
        self.presence = presence


@dataclass
class RoomPresence:
    status: str  # known | stale | unknown | away | not_set_up | no_person
    person: str
    room: str | None = None
    area_id: str | None = None
    age_s: float | None = None
    sensor: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_detail(self) -> dict[str, Any]:
        return {"status": self.status, "room": self.room, "area_id": self.area_id,
                "age_s": None if self.age_s is None else round(self.age_s), "sensor": self.sensor,
                "source": "ble"}


def _tokens(entity_id: str) -> set[str]:
    obj = entity_id.split(".", 1)[-1].lower()
    return {t for t in obj.replace("-", "_").split("_") if t}


def find_person(states: list[dict[str, Any]], person: str) -> dict[str, Any] | None:
    """The ``person.*`` state for a Jarvis username or first name (token match, as geo does)."""
    name = (person or "").strip().lower()
    if not name:
        return None
    people = [s for s in states if str(s.get("entity_id") or "").startswith("person.")]
    exact = [s for s in people if s["entity_id"] == f"person.{name}"]
    if exact:
        return exact[0]
    by_token = [s for s in people if name in _tokens(s["entity_id"])]
    if len(by_token) == 1:
        return by_token[0]
    by_first_name = [s for s in people
                     if str((s.get("attributes") or {}).get("friendly_name") or "").strip().lower().split(" ")[:1] == [name]]
    if len(by_first_name) == 1:
        return by_first_name[0]
    return None


def _seconds(value: Any) -> float | None:
    """An ISO timestamp or epoch seconds as epoch seconds; None when it is neither."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def room_sensors_for(person_state: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The ``sensor.<phone>_room`` states of the person's phones (from its ``device_trackers``)."""
    trackers = (person_state.get("attributes") or {}).get("device_trackers") or []
    out = []
    for tracker in trackers:
        obj = str(tracker).split(".", 1)[-1]
        st = by_id.get(f"sensor.{obj}_room")
        if st is not None:
            out.append(st)
    return out


def presence_from_states(states: list[dict[str, Any]], person: str, *, now: float | None = None,
                         max_age_s: float | None = None) -> RoomPresence:
    """Where ``person`` is, from the hub's states only."""
    now = time.time() if now is None else now
    max_age = ROOM_PRESENCE_MAX_AGE_S if max_age_s is None else max_age_s
    name = (person or "").strip().lower()
    p = find_person(states, name)
    if p is None:
        return RoomPresence("no_person", name)
    by_id = {str(s.get("entity_id")): s for s in states}
    attrs = p.get("attributes") or {}
    sensors = room_sensors_for(p, by_id)
    if not sensors:
        if "room" not in attrs:
            return RoomPresence("not_set_up", name)
        room = str(attrs.get("room") or "")
        if room.lower() in _NO_ROOM:
            return RoomPresence("unknown", name)
        if room == "not_home":
            return RoomPresence("away", name)
        # The person's live attribute with no sensor to date it: the hub's own answer.
        return RoomPresence("known", name, room=room, area_id=attrs.get("room_area_id"))

    def heard(s: dict[str, Any]) -> float:
        return _seconds((s.get("attributes") or {}).get("last_seen")) or 0.0

    sensors.sort(key=heard, reverse=True)
    best = sensors[0]
    state = str(best.get("state") or "")
    sattrs = best.get("attributes") or {}
    seen = _seconds(sattrs.get("last_seen"))
    age = None if seen is None else max(0.0, now - seen)
    base = {"sensor": best.get("entity_id"), "age_s": age}
    if state.lower() in _NO_ROOM:
        return RoomPresence("unknown", name, **base)
    if state == "not_home":
        return RoomPresence("away", name, **base)
    if age is None:
        # A room with no time it was heard cannot be called current.
        return RoomPresence("stale", name, room=state, area_id=sattrs.get("area_id"), **base)
    status = "known" if age <= max_age else "stale"
    return RoomPresence(status, name, room=state, area_id=sattrs.get("area_id") or attrs.get("room_area_id"),
                        extra={k: sattrs.get(k) for k in ("method", "confidence") if k in sattrs}, **base)


async def person_room(person: str, ha_url: str, ha_token: str, *, now: float | None = None) -> RoomPresence:
    """Read the hub's states and answer for ``person``. Raises when the hub cannot be read."""
    from services.execution import ha_client

    states = await ha_client.get_states(ha_url, ha_token, strict=True)
    return presence_from_states(states or [], person, now=now)


async def may_see_room(viewer: str, subject: str) -> bool:
    """Whether ``viewer`` may see ``subject``'s room (identity's raw-data rule; self always).

    Fails closed: when identity cannot answer, nobody else's room is shown.
    """
    from services.common.http import get_client
    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

    v, s = (viewer or "").strip().lower(), (subject or "").strip().lower()
    if not v or not s:
        return False
    if v == s:
        return True
    try:
        async with get_client() as client, client.get(
            f"{IDENTITY_SVC_URL.rstrip('/')}/api/internal/may-view-raw",
            params={"viewer": v, "subject": s}, headers={"X-Internal-Secret": INTERNAL_SECRET},
            timeout=aiohttp.ClientTimeout(total=5.0),
        ) as resp:
            if resp.status != 200:
                log.warning("[room] identity may-view-raw answered HTTP %s; not showing %s's room", resp.status, s)
                return False
            body = await resp.json(content_type=None)
    except Exception as exc:
        log.warning("[room] identity may-view-raw failed (%s); not showing %s's room", type(exc).__name__, s)
        return False
    return bool((body or {}).get("allowed"))


def in_room(room: str) -> str:
    """"in the Den", but "in Alex's Room" and "in the Loft" (no doubled article)."""
    r = room.strip()
    if r.lower().startswith("the ") or "'s " in r or r.endswith("'s"):
        return f"in {r}"
    return f"in the {r}"


def ago(seconds: float | None) -> str:
    if seconds is None:
        return "at an unknown time"
    s = int(seconds)
    if s < 60:
        return "just now"
    m = s // 60
    if m < 60:
        return f"{m} minute{'s' if m != 1 else ''} ago"
    h = m // 60
    return f"{h} hour{'s' if h != 1 else ''} ago"


def why_not(p: RoomPresence) -> str:
    """Plain words for a presence that is not a current room."""
    name = p.person.title()
    if p.status == "stale":
        return f"room detection last heard {name} {in_room(p.room or 'a room')} {ago(p.age_s)}, so I can't say they are still there"
    if p.status == "unknown":
        return f"room detection can't tell which room {name} is in right now"
    if p.status == "away":
        return f"room detection says {name} is not home"
    if p.status == "not_set_up":
        return f"{name}'s phone has no room beacon set up"
    if p.status == "no_person":
        return f"the hub has no person called {name}"
    return f"{name} is {in_room(p.room or '')}"


def room_of_target(target: str) -> str | None:
    """The person in a ``room_of:<person>`` target, else None."""
    t = (target or "").strip()
    if t.lower().startswith(ROOM_OF_PREFIX):
        return t[len(ROOM_OF_PREFIX):].strip().lower() or None
    return None


async def resolve_room_target(person: str, ha_url: str, ha_token: str) -> RoomPresence:
    """The area to send to for "the room <person> is in". Raises RoomTargetError unless it is known."""
    if not ha_url or not ha_token:
        raise RoomTargetError("the hub is not configured for this account, so I can't find anyone's room")
    try:
        p = await person_room(person, ha_url, ha_token)
    except Exception as exc:
        raise RoomTargetError(f"the hub could not be read ({type(exc).__name__}), so I don't know where "
                              f"{person.title()} is") from exc
    if p.status != "known" or not p.room:
        raise RoomTargetError(why_not(p), p)
    return p


async def expand_room_targets(rooms: list[str], ha_url: str, ha_token: str) -> tuple[list[str], list[RoomPresence]]:
    """``rooms`` with every ``room_of:<person>`` replaced by that person's current area name.

    Raises RoomTargetError for one that cannot be resolved; never drops it silently.
    """
    out: list[str] = []
    found: list[RoomPresence] = []
    for r in rooms:
        person = room_of_target(r)
        if person is None:
            out.append(r)
            continue
        p = await resolve_room_target(person, ha_url, ha_token)
        # The area id is unambiguous for the capability index (a name can be an alias elsewhere).
        out.append(p.area_id or p.room or "")
        found.append(p)
    return out, found
