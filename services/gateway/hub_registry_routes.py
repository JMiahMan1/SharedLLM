"""Devices > Home setup: the hub's areas, floors, labels, zones and persons, and its imported categories and tags.

Admins only, like the rest of the Devices page's hub routes. Everything is homehub's registry API
(``services/homehub/entries_api.py`` for areas, ``services/homehub/registries_api.py`` for the rest), reached
through ``discovery_routes._hub`` with the stack's internal secret. Only the fields each route takes are
forwarded, and the hub's answers ("no such floor", "already a zone named ...") keep their status and words.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.gateway import discovery_routes as hub

router = APIRouter(prefix="/api/homehub")

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: Per registry: the hub path, the word for a missing one, and the fields a change may carry.
_KINDS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "areas": ("/hub/areas", "area", ("name", "aliases", "icon", "floor_id", "labels")),
    "floors": ("/hub/floors", "floor", ("name", "level", "icon", "aliases")),
    "labels": ("/hub/labels", "label", ("name", "color", "icon", "description")),
    "zones": ("/hub/zones", "zone", ("name", "latitude", "longitude", "radius", "passive", "icon")),
    "persons": ("/hub/persons", "person", ("name", "jarvis_user", "device_trackers")),
    "network_devices": ("/hub/network_devices", "device", ("mac", "name", "user_id", "device_type",
                                                          "notify_on_arrival", "notify_on_departure",
                                                          "email_on_arrival", "email_on_departure")),
}


def _kind(kind: str) -> tuple[str, str, tuple[str, ...]]:
    found = _KINDS.get(kind)
    if found is None:
        raise HTTPException(status_code=404, detail="Not found")
    return found


def _item(value: str, what: str) -> str:
    if not _ID.match(value or ""):
        raise HTTPException(status_code=404, detail=f"No such {what}")
    return value


async def _send(method: str, path: str, *, json: Any = None) -> Any:
    return hub._answer(*await hub._hub(method, path, json=json))


@router.get("/categories")
async def list_categories(request: Request):
    """The automation, script and scene categories brought by the Home Assistant import."""
    await hub._admin(request)
    return await _send("GET", "/hub/categories")


@router.get("/tags")
async def list_tags(request: Request):
    """The NFC and QR tags with their names (imported; the tag trigger reads them)."""
    await hub._admin(request)
    return await _send("GET", "/hub/tags")


@router.post("/zones/sync")
async def sync_zones(request: Request):
    """Mirror the family's saved places as zones now (the hub also does it every few minutes)."""
    await hub._admin(request)
    return await _send("POST", "/hub/zones/sync")


def _routes(kind: str) -> None:
    """List, create, change and remove for one registry. Each path is spelled out (``/areas``, never
    ``/{kind}``), so these routes can never shadow another ``/api/homehub/...`` route."""
    path, what, fields = _kind(kind)

    async def list_items(request: Request):
        await hub._admin(request)
        return await _send("GET", path)

    async def create_item(request: Request):
        await hub._admin(request)
        body = await hub._json(request)
        return await _send("POST", path, json={k: body[k] for k in fields if k in body})

    async def update_item(item_id: str, request: Request):
        await hub._admin(request)
        body = await hub._json(request)
        return await _send("PATCH", f"{path}/{_item(item_id, what)}",
                           json={k: body[k] for k in fields if k in body})

    async def delete_item(item_id: str, request: Request):
        await hub._admin(request)
        return await _send("DELETE", f"{path}/{_item(item_id, what)}")

    router.add_api_route(f"/{kind}", list_items, methods=["GET"], name=f"list_{kind}")
    router.add_api_route(f"/{kind}", create_item, methods=["POST"], name=f"create_{what}")
    router.add_api_route(f"/{kind}/{{item_id}}", update_item, methods=["PATCH"], name=f"update_{what}")
    router.add_api_route(f"/{kind}/{{item_id}}", delete_item, methods=["DELETE"], name=f"delete_{what}")


for _k in _KINDS:
    _routes(_k)


# --- Network device extra endpoints -----------------------------------------------------------

_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", re.IGNORECASE)


def _mac(mac: str) -> str:
    mac = mac.strip().lower()
    if not _MAC_RE.match(mac):
        raise HTTPException(status_code=404, detail="Invalid MAC address")
    return mac


@router.get("/network_devices/status")
async def network_devices_status(request: Request):
    """All known devices with their current network status (Admin > Network > Devices)."""
    await hub._admin(request)
    return await hub._answer(*await hub._hub("GET", "/hub/network_devices/status"))


@router.get("/network_devices/{mac}/history")
async def network_device_history(mac: str, request: Request, hours: int = 168):
    """Arrival/departure history for a device (last N hours, default 1 week)."""
    await hub._admin(request)
    mac = _mac(mac)
    return await hub._answer(*await hub._hub("GET", f"/hub/network_devices/{mac}/history", params={"hours": hours}))
