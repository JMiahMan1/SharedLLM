"""Devices > Home setup: the hub's areas, floors, labels, zones, persons, users and login providers.

Also its imported categories and tags.

Admins only, like the rest of the Devices page's hub routes. Everything is homehub's registry API
(``services/homehub/entries_api.py`` for areas, ``services/homehub/registries_api.py`` for the rest), reached
through ``discovery_routes._hub`` with the stack's internal secret. Only the fields each route takes are
forwarded, and the hub's answers ("no such floor", "already a zone named ...") keep their status and words.

Zones are every place the family has (HomeHub is the only store of places). The admin's own username goes
with each zone call as ``viewer``, so the list holds the family's places and the admin's own private ones,
never someone else's private place. ``/zones/home`` is Home: it moves and resizes, and is never removed.
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
    "zones": ("/hub/zones", "zone", ("name", "latitude", "longitude", "radius", "passive", "icon", "important",
                                      "notify", "pin")),
    "persons": ("/hub/persons", "person", ("name", "jarvis_user", "device_trackers")),
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


async def _send(method: str, path: str, *, json: Any = None, params: dict[str, str] | None = None) -> Any:
    return hub._answer(*await hub._hub(method, path, json=json, params=params))


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


# -- users and login providers (services/homehub/users_api.py) ---------------------------------------

_PROVIDER = re.compile(r"^[a-z0-9_]{2,40}$")


@router.get("/auth/providers")
async def login_providers(request: Request):
    """Where the hub's users and logins come from: the hub itself, Jarvis; primary; last sync; hub logins."""
    await hub._admin(request)
    return await _send("GET", "/hub/auth/providers")


@router.put("/auth/providers")
async def set_login_providers(request: Request):
    """``{"primary"?, "local_logins"?}``; the hub refuses (409) a change that would leave no admin able to log in."""
    await hub._admin(request)
    body = await hub._json(request)
    return await _send("PUT", "/hub/auth/providers", json={k: body[k] for k in ("primary", "local_logins") if k in body})


@router.post("/auth/providers/{kind}/sync")
async def sync_login_provider(kind: str, request: Request):
    """Mirror a provider's users and persons into the hub now."""
    await hub._admin(request)
    if not _PROVIDER.match(kind or ""):
        raise HTTPException(status_code=404, detail="No such login provider")
    return await _send("POST", f"/hub/auth/providers/{kind}/sync")


@router.get("/users")
async def hub_users(request: Request):
    """The hub's users (no credentials): which come from Jarvis and which are the hub's own."""
    await hub._admin(request)
    return await _send("GET", "/hub/users")


@router.post("/users")
async def add_hub_user(request: Request):
    """A user of the hub only: ``{"name", "username", "password", "is_admin"?}``."""
    await hub._admin(request)
    body = await hub._json(request)
    return await _send("POST", "/hub/users", json={k: body[k] for k in ("name", "username", "password", "is_admin")
                                                   if k in body})


@router.patch("/users/{user_id}")
async def change_hub_user(user_id: str, request: Request):
    """``{"name"?, "is_admin"?, "is_active"?, "password"?}`` for one of the hub's own users."""
    await hub._admin(request)
    body = await hub._json(request)
    return await _send("PATCH", f"/hub/users/{_item(user_id, 'user')}",
                       json={k: body[k] for k in ("name", "is_admin", "is_active", "password") if k in body})


def _routes(kind: str) -> None:
    """List, create, change and remove for one registry. Each path is spelled out (``/areas``, never
    ``/{kind}``), so these routes can never shadow another ``/api/homehub/...`` route."""
    path, what, fields = _kind(kind)

    async def who(request: Request) -> dict[str, str] | None:
        """Zones are places: the hub shows the admin the family's and their own, never another's private one."""
        user = await hub._admin(request)
        return {"viewer": user} if kind == "zones" else None

    async def changed() -> None:
        if kind == "zones":
            from services.gateway import family_safety

            await family_safety.places_changed()

    async def list_items(request: Request):
        return await _send("GET", path, params=await who(request))

    async def create_item(request: Request):
        params = await who(request)
        body = await hub._json(request)
        out = await _send("POST", path, json={k: body[k] for k in fields if k in body}, params=params)
        await changed()
        return out

    async def update_item(item_id: str, request: Request):
        params = await who(request)
        body = await hub._json(request)
        out = await _send("PATCH", f"{path}/{_item(item_id, what)}",
                          json={k: body[k] for k in fields if k in body}, params=params)
        await changed()
        return out

    async def delete_item(item_id: str, request: Request):
        params = await who(request)
        out = await _send("DELETE", f"{path}/{_item(item_id, what)}", params=params)
        await changed()
        return out

    router.add_api_route(f"/{kind}", list_items, methods=["GET"], name=f"list_{kind}")
    router.add_api_route(f"/{kind}", create_item, methods=["POST"], name=f"create_{what}")
    router.add_api_route(f"/{kind}/{{item_id}}", update_item, methods=["PATCH"], name=f"update_{what}")
    router.add_api_route(f"/{kind}/{{item_id}}", delete_item, methods=["DELETE"], name=f"delete_{what}")


for _k in _KINDS:
    _routes(_k)
