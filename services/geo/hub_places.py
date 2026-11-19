"""The family's places, read from HomeHub (the only store of places).

Home, the zones drawn on Devices > Rooms & places (or imported from Home Assistant) and the places people
save from Wander are all HomeHub zones (``services/homehub/registries.py``). Geo keeps none of its own: it
reads every place, private ones included, from ``GET /hub/zones?all=1`` with the stack's internal secret, and
uses them for arrivals and departures, place reminders, visit suggestions, long stops and naming spots.

A private place is its owner's alone: only the owner's own movements are matched against it
(``for_person``), and anything another person can see (a day's stops, a trip's ends, ``/zones`` for a viewer)
uses the family's places only (``family``).

With no HomeHub set up there are no places at all (``PlacesUnavailable``), never a second store.

``migrate`` is the one-time move of the places geo used to keep in Redis (``geo:places``, and the
important/notify/icon per zone in ``geo:place_settings``) into HomeHub. It is idempotent, logs its counts,
and leaves geo's old keys exactly as they were, as a backup until a later cleanup.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from services.common.http import get_client
from services.common.hub_login import NO_HOMEHUB_MESSAGE, stack_login

log = logging.getLogger("geo.hub_places")

TIMEOUT = aiohttp.ClientTimeout(total=10)
#: How a private place is known (it has no ``zone.*`` entity on the hub).
PRIVATE_PREFIX = "place."
#: Geo's old stores, read once by ``migrate`` and never written again.
LEGACY_PLACES_KEY = "geo:places"
LEGACY_SETTINGS_KEY = "geo:place_settings"
#: Set once the move succeeded: ``{"at", "created", "kept", ...}``.
MIGRATED_KEY = "geo:places_moved_to_homehub"


class PlacesUnavailable(Exception):
    """No HomeHub to read places from, or it did not answer. ``status`` is what a route should say."""

    def __init__(self, message: str, status: int = 503) -> None:
        super().__init__(message)
        self.status = status


def _target() -> tuple[str, str]:
    login = stack_login()
    if login is None:
        raise PlacesUnavailable(NO_HOMEHUB_MESSAGE)
    return login[0].rstrip("/"), login[1]


async def _call(method: str, path: str, *, params: dict[str, str] | None = None, json_body: Any = None) -> Any:
    base, secret = _target()
    try:
        async with get_client().request(method, f"{base}{path}", params=params, json=json_body,
                                        headers={"Authorization": f"Bearer {secret}"}, timeout=TIMEOUT) as resp:
            body = await resp.json(content_type=None)
            status = resp.status
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        raise PlacesUnavailable(f"HomeHub did not answer for places: {type(exc).__name__}: {exc}", 502) from exc
    if status != 200:
        message = (body or {}).get("message") if isinstance(body, dict) else None
        raise PlacesUnavailable(f"HomeHub answered HTTP {status} for places: {message or ''}".rstrip(": "), 502)
    return body


def to_zone(z: dict[str, Any]) -> dict[str, Any] | None:
    """A HomeHub zone in the shape geo's presence code uses, or None for one with no location."""
    if z.get("latitude") is None or z.get("longitude") is None or not z.get("entity_id"):
        return None
    return {
        "id": str(z.get("id") or ""),
        "entity_id": str(z["entity_id"]),
        "name": str(z.get("name") or "Place"),
        "latitude": float(z["latitude"]),
        "longitude": float(z["longitude"]),
        "radius": float(z.get("radius") or 100.0),
        "passive": bool(z.get("passive")),
        # The Jarvis username of whoever saved it (``owner`` on the hub is the hub user it belongs to).
        "owner": z.get("owner_name") or None,
        "private": z.get("visibility") == "private",
        # A place someone saved (it has an owner), as opposed to Home or a zone drawn for the household.
        "custom": bool(z.get("owner_name") or z.get("owner")),
        "important": bool(z.get("important")),
        "notify": [str(u) for u in z.get("notify") or []],
        "pin": str(z.get("pin") or ""),
        "created_at": z.get("created_at"),
    }


async def fetch_all() -> list[dict[str, Any]]:
    """Every place, private ones included (for presence). Raises PlacesUnavailable."""
    body = await _call("GET", "/hub/zones", params={"all": "1"})
    zones = body.get("zones") if isinstance(body, dict) else None
    if not isinstance(zones, list):
        raise PlacesUnavailable("HomeHub's answer for places had no zones", 502)
    return [z for z in (to_zone(item) for item in zones if isinstance(item, dict)) if z]


def family(zones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The places everyone may see: no one's private place."""
    return [z for z in zones if not z.get("private")]


def for_person(zones: list[dict[str, Any]], user: str | None) -> list[dict[str, Any]]:
    """The family's places plus ``user``'s own private ones: what that person's own movements match."""
    who = (user or "").strip().lower()
    return [z for z in zones if not z.get("private") or (who and str(z.get("owner") or "").lower() == who)]


def public_zone_id(entity_id: str | None) -> str | None:
    """A zone id safe to show someone else: a private place's id is not."""
    if not entity_id or str(entity_id).startswith(PRIVATE_PREFIX):
        return None
    return str(entity_id)


def _decode(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def _legacy(r) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    places = []
    for raw in (await r.hgetall(LEGACY_PLACES_KEY) or {}).values():
        try:
            place = json.loads(_decode(raw))
        except (TypeError, ValueError):
            continue
        if isinstance(place, dict):
            places.append(place)
    settings = {}
    for key, raw in (await r.hgetall(LEGACY_SETTINGS_KEY) or {}).items():
        try:
            entry = json.loads(_decode(raw))
        except (TypeError, ValueError):
            continue
        if isinstance(entry, dict):
            settings[_decode(key)] = entry
    return places, settings


async def _carry_over(r, mapping: dict[str, str | None]) -> int:
    """Point presence state and reminders that named an old ``place.<id>`` at what it is now."""
    changed = 0
    renamed = {old: new for old, new in mapping.items() if new and new != old}
    if not renamed:
        return 0
    async for key in r.scan_iter(match="geo:zone_state:*"):
        raw = await r.get(key)
        try:
            state = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            continue
        if not isinstance(state, dict):
            continue
        touched = False
        for field in ("zone", "pending"):
            if state.get(field) in renamed:
                state[field] = renamed[state[field]]
                touched = True
        if touched:
            await r.set(key, json.dumps(state))
            changed += 1
    async for key in r.scan_iter(match="geo:place_reminders:*"):
        for rid, raw in (await r.hgetall(key) or {}).items():
            try:
                entry = json.loads(_decode(raw))
            except (TypeError, ValueError):
                continue
            if isinstance(entry, dict) and entry.get("zone") in renamed:
                entry["zone"] = renamed[entry["zone"]]
                await r.hset(key, _decode(rid), json.dumps(entry))
                changed += 1
    return changed


async def migrate(r, now: float) -> dict[str, Any] | None:
    """Move geo's saved places and place settings into HomeHub, once.

    Returns what was done, or None when there was nothing to do (already moved, or nothing stored).
    Raises PlacesUnavailable while HomeHub cannot take them, so the caller tries again later. Geo's own
    keys are only read: they stay as a backup.
    """
    if await r.get(MIGRATED_KEY):
        return None
    places, settings = await _legacy(r)
    if not places and not settings:
        await r.set(MIGRATED_KEY, json.dumps({"at": now, "places": 0, "settings": 0}))
        return None
    result = await _call("POST", "/hub/zones/import", json_body={"places": places, "settings": settings})
    mapping = result.get("entity_ids") or {} if isinstance(result, dict) else {}
    carried = await _carry_over(r, mapping)
    report = {"at": now, "places": len(places), "settings": len(settings),
              **{k: result.get(k) for k in ("created", "kept", "skipped", "settings_kept", "unmatched")},
              "settings_moved": result.get("settings"), "state_carried": carried}
    await r.set(MIGRATED_KEY, json.dumps(report))
    log.info("[Geo] places moved to HomeHub: %s (geo's own copy is kept as a backup)", report)
    return report


def as_saved_place(z: dict[str, Any]) -> dict[str, Any]:
    """A place (``to_zone``) in the shape the account export always used (geo's old saved-place record)."""
    return {"id": z["id"], "name": z["name"], "lat": z["latitude"], "lon": z["longitude"], "radius": z["radius"],
            "scope": "user" if z.get("private") else "family", "owner": z.get("owner") or "",
            "important": bool(z.get("important")), "notify": list(z.get("notify") or []), "icon": z.get("pin") or "",
            **({"created_at": z["created_at"]} if z.get("created_at") is not None else {})}


async def import_places(places: list[dict[str, Any]]) -> dict[str, Any]:
    """Saved-place records (``as_saved_place`` shape) into HomeHub, keeping their ids (idempotent)."""
    return await _call("POST", "/hub/zones/import", json_body={"places": places, "settings": {}})


async def update_place(place_id: str, owner: str, place: dict[str, Any]) -> Any:
    """Overwrite one of ``owner``'s places from a saved-place record."""
    body = {"name": place.get("name"), "latitude": place.get("lat"), "longitude": place.get("lon"),
            "radius": place.get("radius"), "visibility": "family" if place.get("scope") == "family" else "private",
            "important": bool(place.get("important")), "notify": list(place.get("notify") or []),
            "pin": place.get("icon") or ""}
    return await _call("PATCH", f"/hub/zones/{place_id}", params={"viewer": owner},
                       json_body={k: v for k, v in body.items() if v is not None})
