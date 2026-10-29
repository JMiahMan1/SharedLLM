"""Family and personal places, saved from the Wander screen.

Home Assistant zones are the places the household already draws in HA. This
module adds places the family makes from Wander: one shared family list plus
each person's own private places. A saved place is a point with a radius, so
geo's presence code treats it exactly like an HA zone - arriving at or leaving
one can send a ``family_safety`` notice to the people chosen for it.

Only Redis lives here; ``main.py`` wires the store to the HTTP routes and to
the presence transitions.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

#: Redis hash: place id -> the place record as JSON.
PLACES_KEY = "geo:places"
#: Saved places appear to the rest of geo as ``place.<id>`` entities.
PLACE_PREFIX = "place."
MAX_PLACES = 200
NAME_MAX = 80
DEFAULT_RADIUS_M = 150.0
MIN_RADIUS_M = 50.0
MAX_RADIUS_M = 5000.0
#: "family" is shared with the household, "user" is private to its owner.
SCOPES = ("family", "user")

_PLACE_ID = re.compile(r"^[0-9a-f]{32}$")


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def entity_id(place_id: str) -> str:
    """The ``place.<id>`` entity a saved place is known by inside geo."""
    return f"{PLACE_PREFIX}{place_id}"


def place_id_from_entity(value: str) -> str | None:
    """The place id behind a ``place.<id>`` entity, or None for anything else."""
    if not isinstance(value, str) or not value.startswith(PLACE_PREFIX):
        return None
    candidate = value[len(PLACE_PREFIX):]
    return candidate if _PLACE_ID.match(candidate) else None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate(body: Any, *, partial: bool = False) -> dict[str, Any]:
    """A place from the Wander form. Raises ValueError with the reason.

    ``partial`` lets a change carry only the fields it is changing, which is
    what editing an existing place sends; a brand new place must name itself
    and say where it is.
    """
    if not isinstance(body, dict):
        raise ValueError("a place must be an object")
    out: dict[str, Any] = {}
    name = body.get("name")
    if name is not None:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name is required")
        out["name"] = name.strip()[:NAME_MAX]
    elif not partial:
        raise ValueError("name is required")
    if "lat" in body or not partial:
        lat = _number(body.get("lat"))
        if lat is None or not -90.0 <= lat <= 90.0:
            raise ValueError("lat must be a number between -90 and 90")
        out["lat"] = lat
    if "lon" in body or not partial:
        lon = _number(body.get("lon"))
        if lon is None or not -180.0 <= lon <= 180.0:
            raise ValueError("lon must be a number between -180 and 180")
        out["lon"] = lon
    if "radius" in body or not partial:
        radius = _number(body.get("radius"))
        out["radius"] = DEFAULT_RADIUS_M if radius is None else min(max(radius, MIN_RADIUS_M), MAX_RADIUS_M)
    if "scope" in body or not partial:
        scope = body.get("scope") or "user"
        if scope not in SCOPES:
            raise ValueError("scope must be 'family' or 'user'")
        out["scope"] = scope
    if "important" in body or not partial:
        out["important"] = bool(body.get("important"))
    if "notify" in body or not partial:
        notify = body.get("notify")
        if notify is None:
            notify = []
        if not isinstance(notify, list) or not all(isinstance(item, str) for item in notify):
            raise ValueError("notify must be a list of usernames")
        out["notify"] = sorted({item.strip().lower() for item in notify if item.strip()})
    return out


def _record(raw: Any) -> dict[str, Any] | None:
    try:
        place = json.loads(_decode(raw))
    except (TypeError, ValueError):
        return None
    if not isinstance(place, dict) or not _PLACE_ID.match(str(place.get("id", ""))):
        return None
    return place


async def list_all(r) -> list[dict[str, Any]]:
    """Every saved place, family and private alike (for presence matching)."""
    out = [place for place in (_record(raw) for raw in (await r.hgetall(PLACES_KEY)).values()) if place]
    return sorted(out, key=lambda place: str(place.get("name", "")).lower())


async def list_for(r, viewer: str) -> list[dict[str, Any]]:
    """The places a viewer may see: the family list plus their own private ones."""
    who = (viewer or "").strip().lower()
    return [place for place in await list_all(r) if can_see(place, who)]


async def get(r, place_id: str) -> dict[str, Any] | None:
    if not _PLACE_ID.match(str(place_id or "")):
        return None
    return _record(await r.hget(PLACES_KEY, place_id))


async def add(r, owner: str, place: dict[str, Any]) -> dict[str, Any]:
    """Save a new place owned by ``owner``. Raises ValueError when the shelf is full."""
    if len(await list_all(r)) >= MAX_PLACES:
        raise ValueError(f"You already have {MAX_PLACES} saved places")
    entry = {
        "id": uuid.uuid4().hex,
        "owner": (owner or "").strip().lower(),
        "created_at": time.time(),
        **place,
    }
    await r.hset(PLACES_KEY, entry["id"], json.dumps(entry))
    return entry


async def update(r, place_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    """Apply a validated change set to a place, keeping its id, owner and age."""
    place = await get(r, place_id)
    if place is None:
        return None
    merged = {**place, **changes}
    await r.hset(PLACES_KEY, place["id"], json.dumps(merged))
    return merged


async def remove(r, place_id: str) -> bool:
    if not _PLACE_ID.match(str(place_id or "")):
        return False
    if await r.hget(PLACES_KEY, place_id) is None:
        return False
    await r.hdel(PLACES_KEY, place_id)
    return True


def can_see(place: dict[str, Any], viewer: str) -> bool:
    """A family place is everyone's; a private place only its owner's."""
    return place.get("scope") == "family" or place.get("owner") == viewer


def can_edit(place: dict[str, Any], viewer: str, is_admin: bool = False) -> bool:
    """The person who saved a place can change it; an admin can change any."""
    return bool(is_admin) or place.get("owner") == viewer


def to_zone(place: dict[str, Any]) -> dict[str, Any]:
    """A saved place in the shape the presence code uses for an HA zone."""
    return {
        "entity_id": entity_id(str(place.get("id", ""))),
        "name": place.get("name") or "Saved place",
        "latitude": float(place.get("lat", 0.0)),
        "longitude": float(place.get("lon", 0.0)),
        "radius": float(place.get("radius", DEFAULT_RADIUS_M)),
        "custom": True,
        "owner": place.get("owner", ""),
        "scope": place.get("scope", "user"),
    }


def to_answer(place: dict[str, Any]) -> dict[str, Any]:
    """A saved place for the UI: its own record plus the entity geo knows it by."""
    return {**place, "entity_id": entity_id(str(place.get("id", "")))}


def alert_for(place: dict[str, Any]) -> dict[str, Any]:
    """The important/notify pair the presence code reads, in safety.py's shape."""
    return {
        "important": bool(place.get("important")),
        "notify": list(place.get("notify") or []),
    }
