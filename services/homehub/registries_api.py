"""The ``/hub/floors``, ``/hub/labels``, ``/hub/categories``, ``/hub/zones``, ``/hub/persons`` and ``/hub/tags`` API.

| Route | What |
|---|---|
| ``GET /hub/floors``, ``POST /hub/floors`` | The floors; ``{"name", "level"?, "icon"?, "aliases"?}`` makes one |
| ``PATCH /hub/floors/{id}``, ``DELETE /hub/floors/{id}`` | Change one; remove one (its areas stay, on no floor) |
| ``GET /hub/labels``, ``POST /hub/labels`` | The labels; ``{"name", "color"?, "icon"?, "description"?}`` makes one |
| ``PATCH /hub/labels/{id}``, ``DELETE /hub/labels/{id}`` | Change one; remove one (and take it off everything) |
| ``GET /hub/categories`` | The imported automation/script/scene categories, by scope |
| ``GET /hub/zones``, ``POST /hub/zones`` | Every place: Home, the drawn zones and the places saved in Wander; ``{"name", "latitude", "longitude", "radius"?, "passive"?, "icon"?, "important"?, "notify"?, "pin"?}`` makes one |
| ``GET``, ``PATCH``, ``DELETE /hub/zones/{id}`` | One place; change it; remove it. ``home`` is Home: it moves (``latitude``, ``longitude``, ``radius``, ``name``) but is never removed (409) |
| ``POST /hub/zones/import`` | Geo's one-time move of its saved places and place settings (internal secret only; idempotent) |
| ``GET /hub/persons``, ``POST /hub/persons`` | The persons and the trackers they can use; ``{"name", "jarvis_user"?, "device_trackers"?}`` |
| ``PATCH /hub/persons/{id}``, ``DELETE /hub/persons/{id}`` | Change or remove a person |
| ``GET /hub/tags`` | The imported NFC/QR tags with their names |

Admins only, like the rest of ``/hub/*``. See ``registries.py``.

Places belong to the hub's own users. A hub login sees and changes the family's places and its own private
ones, and what it makes is its own. The stack's internal secret (Jarvis's gateway and geo) may pass ``viewer``
(a Jarvis username, matched to the hub user it belongs to) to act for that person, or ``all=1`` (geo's
presence) for every place. Nobody sees someone else's private place, admins included; the gateway decides
who may change which place (the owner, or an admin for anything not private).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub import registries
from services.homehub.auth import Auth, Principal, bearer
from services.homehub.core import HubError


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()

    def admin(request: Request) -> Principal:
        tok = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(tok)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")
        return p

    def scope(request: Request) -> dict[str, Any]:
        """Whose places the caller sees. A hub login sees the family's places and its own private ones; the
        stack's own secret may act for a Jarvis user (``viewer``) or, for geo's presence, see all (``all``)."""
        p = admin(request)
        if p.user_id != "internal":
            return {"who": registries.Who(user_id=p.user_id), "everything": False}
        q = request.query_params
        return {"who": registries.who_for(runtime, jarvis_user=q.get("viewer")) if q.get("viewer") else None,
                "everything": q.get("all") in ("1", "true")}

    def trusted(request: Request) -> bool:
        return admin(request).user_id == "internal"

    async def body_of(request: Request) -> dict[str, Any]:
        admin(request)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        return body

    async def run(request: Request, fn: Callable[[], Any | Awaitable[Any]]) -> Any:
        admin(request)
        try:
            out = fn()
            return await out if isinstance(out, Awaitable) else out
        except HubError as exc:
            status = int(getattr(exc, "status", 400) or 400)
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=status)

    @router.get("/hub/floors")
    async def list_floors(request: Request) -> Any:
        return await run(request, lambda: registries.list_floors(runtime))

    @router.post("/hub/floors")
    async def create_floor(request: Request) -> Any:
        body = await body_of(request)
        return await run(request, lambda: registries.create_floor(runtime, body))

    @router.patch("/hub/floors/{floor_id}")
    async def update_floor(floor_id: str, request: Request) -> Any:
        body = await body_of(request)
        return await run(request, lambda: registries.update_floor(runtime, floor_id, body))

    @router.delete("/hub/floors/{floor_id}")
    async def delete_floor(floor_id: str, request: Request) -> Any:
        return await run(request, lambda: registries.delete_floor(runtime, floor_id))

    @router.get("/hub/labels")
    async def list_labels(request: Request) -> Any:
        return await run(request, lambda: registries.list_labels(runtime))

    @router.post("/hub/labels")
    async def create_label(request: Request) -> Any:
        body = await body_of(request)
        return await run(request, lambda: registries.create_label(runtime, body))

    @router.patch("/hub/labels/{label_id}")
    async def update_label(label_id: str, request: Request) -> Any:
        body = await body_of(request)
        return await run(request, lambda: registries.update_label(runtime, label_id, body))

    @router.delete("/hub/labels/{label_id}")
    async def delete_label(label_id: str, request: Request) -> Any:
        return await run(request, lambda: registries.delete_label(runtime, label_id))

    @router.get("/hub/categories")
    async def list_categories(request: Request) -> Any:
        return await run(request, lambda: registries.list_categories(runtime))

    @router.get("/hub/tags")
    async def list_tags(request: Request) -> Any:
        return await run(request, lambda: registries.list_tags(runtime))

    @router.get("/hub/zones")
    async def list_zones(request: Request) -> Any:
        who = scope(request)
        return await run(request, lambda: registries.list_zones(runtime, **who))

    @router.post("/hub/zones/import")
    async def import_places(request: Request) -> Any:
        body = await body_of(request)
        if not trusted(request):
            raise HTTPException(403, detail="Only Jarvis's own services import places")
        return await run(request, lambda: registries.import_places(runtime, body))

    @router.post("/hub/zones")
    async def create_zone(request: Request) -> Any:
        body = await body_of(request)
        p = admin(request)
        inside = p.user_id == "internal"
        return await run(request, lambda: registries.create_zone(runtime, body, trusted=inside,
                                                                 creator=None if inside else p.user_id))

    @router.get("/hub/zones/{zone_id}")
    async def get_zone(zone_id: str, request: Request) -> Any:
        who = scope(request)
        return await run(request, lambda: registries.get_zone(runtime, zone_id, **who))

    @router.patch("/hub/zones/{zone_id}")
    async def update_zone(zone_id: str, request: Request) -> Any:
        body = await body_of(request)
        who, inside = scope(request), trusted(request)
        return await run(request, lambda: registries.update_zone(runtime, zone_id, body, trusted=inside, **who))

    @router.delete("/hub/zones/{zone_id}")
    async def delete_zone(zone_id: str, request: Request) -> Any:
        who = scope(request)
        return await run(request, lambda: registries.delete_zone(runtime, zone_id, **who))

    @router.get("/hub/persons")
    async def list_persons(request: Request) -> Any:
        return await run(request, lambda: registries.list_persons(runtime))

    @router.post("/hub/persons")
    async def create_person(request: Request) -> Any:
        body = await body_of(request)
        return await run(request, lambda: registries.create_person(runtime, body))

    @router.patch("/hub/persons/{person_id}")
    async def update_person(person_id: str, request: Request) -> Any:
        body = await body_of(request)
        return await run(request, lambda: registries.update_person(runtime, person_id, body))

    @router.delete("/hub/persons/{person_id}")
    async def delete_person(person_id: str, request: Request) -> Any:
        return await run(request, lambda: registries.delete_person(runtime, person_id))

    return router
