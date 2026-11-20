"""Indoor rooms are Home Hub's areas -- the house's one list of rooms.

Home Hub owns the area registry (``services/homehub/entries_api.py``:
``GET/POST /hub/areas`` and ``PATCH/DELETE /hub/areas/{area_id}``), so Garden
keeps no room list of its own. ``GET /locations`` merges those areas (as
``kind="indoor_room"``) with the places Garden really does own -- raised beds,
herb stations, greenhouses -- which the hub knows nothing about, and adding,
renaming or removing a room is forwarded to the hub.

Every call degrades instead of raising. With no ``HOMEHUB_URL`` or a hub that
does not answer, :func:`list_rooms` returns ``None`` and callers fall back to
the garden's own rows, so a hub that is away never 500s the Garden page and a
plant keeps the room name it was given.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

from services import config

log = logging.getLogger("garden.hub_rooms")

#: The hub answers in milliseconds on the LAN; a slow one must not stall a page.
HUB_TIMEOUT_S = 5.0

#: How long a room list is reused. Long enough that a page of plants does not
#: ask the hub once per plant, short enough that a rename shows up promptly.
_CACHE_TTL_S = 15.0

#: ``(read at, areas)``; ``None`` until the first successful read.
_cache: tuple[float, list[dict[str, Any]]] | None = None


def enabled() -> bool:
    """Is there a hub to ask? (``HOMEHUB_URL``, resolved once at startup.)"""
    return bool(config.HOMEHUB_URL)


def invalidate() -> None:
    """Forget the cached rooms: the next read asks the hub again."""
    global _cache
    _cache = None


async def _call(method: str, path: str, *, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    """One request to the hub. ``(0, None)`` when it is off, unreachable or slow."""
    if not enabled():
        return (0, None)
    url = f"{config.HOMEHUB_URL.rstrip('/')}{path}"
    headers = {"X-Internal-Secret": config.INTERNAL_SECRET}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=HUB_TIMEOUT_S)
        ) as client, client.request(method, url, json=body, headers=headers) as resp:
            try:
                data = await resp.json(content_type=None)
            except ValueError:
                data = None
            return (resp.status, data)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
        log.warning("[rooms] Home Hub did not answer %s %s (%s)", method, path, exc)
        return (0, None)



def _areas(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("areas"), list):
        return [a for a in data["areas"] if isinstance(a, dict) and a.get("area_id") and a.get("name")]
    return []


async def list_rooms(*, fresh: bool = False) -> list[dict[str, Any]] | None:
    """The hub's areas, or ``None`` when there is no hub (or it is not answering)."""
    global _cache
    if not enabled():
        return None
    now = time.monotonic()
    if not fresh and _cache is not None and now - _cache[0] < _CACHE_TTL_S:
        return _cache[1]
    status, data = await _call("GET", "/hub/areas")
    if status != 200:
        return None
    areas = _areas(data)
    _cache = (time.monotonic(), areas)
    return areas


async def room(area_id: str | None) -> dict[str, Any] | None:
    """One room by id, from the cached list."""
    if not area_id:
        return None
    for area in await list_rooms() or []:
        if area.get("area_id") == area_id:
            return area
    return None


async def create_room(name: str) -> dict[str, Any] | None:
    """Add a room to the hub. ``None`` when the hub refused or did not answer."""
    status, data = await _call("POST", "/hub/areas", body={"name": name})
    if status not in (200, 201) or not isinstance(data, dict) or not data.get("area_id"):
        log.warning("[rooms] Home Hub refused to add %r (HTTP %s: %s)", name, status, data)
        return None
    invalidate()
    return data


async def update_room(area_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    """Rename a room (or change its icon/floor) in the hub."""
    status, data = await _call("PATCH", f"/hub/areas/{area_id}", body=fields)
    if status != 200 or not isinstance(data, dict) or not data.get("area_id"):
        log.warning("[rooms] Home Hub refused to change %s (HTTP %s: %s)", area_id, status, data)
        return None
    invalidate()
    return data


async def delete_room(area_id: str) -> bool:
    """Remove a room from the hub. Its devices keep working, with no room."""
    status, _ = await _call("DELETE", f"/hub/areas/{area_id}")
    if status not in (200, 204):
        log.warning("[rooms] Home Hub refused to remove %s (HTTP %s)", area_id, status)
        return False
    invalidate()
    return True


def room_view(area: dict[str, Any]) -> dict[str, Any]:
    """A hub area in the shape ``LocationOut`` uses, so a room reads like a place.

    Garden-only columns (width, sun, yard-map coordinates, assignments) stay
    empty: the hub has no idea what they mean, and they are not editable here.
    """
    return {
        "id": str(area["area_id"]),
        "name": str(area["name"]),
        "kind": "indoor_room",
        "area_id": str(area["area_id"]),
        "width_ft": None,
        "length_ft": None,
        "area_sqft": None,
        "sun_exposure": None,
        "covered": False,
        "map_x": None,
        "map_y": None,
        "rotation_deg": None,
        "light_notes": None,
        "draft_notes": None,
        "assigned_to": None,
        "skylight_sync_enabled": False,
        "source": "homehub",
        "created_at": "",
        "updated_at": "",
    }


@dataclass(frozen=True)
class HubRoom:
    """A hub area as the rest of the service reads it: a place a plant sits in.

    The fields the care paths look at are here (``name``, ``kind``, ``covered``,
    ``assigned_to``); a room is never outdoors and never carries a Garden
    assignment, so both stay empty.
    """

    id: str
    name: str
    kind: str = "indoor_room"
    covered: bool = False
    assigned_to: str | None = None
    skylight_sync_enabled: bool = False


def as_place(area: dict[str, Any]) -> HubRoom:
    return HubRoom(id=str(area["area_id"]), name=str(area["name"]))
