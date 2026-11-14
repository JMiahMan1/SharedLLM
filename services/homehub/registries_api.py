"""The ``/hub/floors``, ``/hub/labels``, ``/hub/categories``, ``/hub/zones``, ``/hub/persons`` and ``/hub/tags`` API.

| Route | What |
|---|---|
| ``GET /hub/floors``, ``POST /hub/floors`` | The floors; ``{"name", "level"?, "icon"?, "aliases"?}`` makes one |
| ``PATCH /hub/floors/{id}``, ``DELETE /hub/floors/{id}`` | Change one; remove one (its areas stay, on no floor) |
| ``GET /hub/labels``, ``POST /hub/labels`` | The labels; ``{"name", "color"?, "icon"?, "description"?}`` makes one |
| ``PATCH /hub/labels/{id}``, ``DELETE /hub/labels/{id}`` | Change one; remove one (and take it off everything) |
| ``GET /hub/categories`` | The imported automation/script/scene categories, by scope |
| ``GET /hub/zones``, ``POST /hub/zones`` | Home, the drawn zones and the saved-place zones; ``{"name", "latitude", "longitude", "radius"?, "passive"?, "icon"?}`` makes one |
| ``PATCH /hub/zones/{id}``, ``DELETE /hub/zones/{id}`` | Change or remove a drawn zone (home and saved places answer 409) |
| ``POST /hub/zones/sync`` | Follow the family's saved places now (the hub also does it every few minutes) |
| ``GET /hub/persons``, ``POST /hub/persons`` | The persons and the trackers they can use; ``{"name", "jarvis_user"?, "device_trackers"?}`` |
| ``PATCH /hub/persons/{id}``, ``DELETE /hub/persons/{id}`` | Change or remove a person |
| ``GET /hub/tags`` | The imported NFC/QR tags with their names |

Admins only, like the rest of ``/hub/*``. See ``registries.py``.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub import registries
from services.homehub.auth import Auth, bearer
from services.homehub.core import HubError


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()

    def admin(request: Request) -> None:
        tok = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(tok)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")

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
        return await run(request, lambda: registries.list_zones(runtime))

    @router.post("/hub/zones/sync")
    async def sync_zones(request: Request) -> Any:
        return await run(request, lambda: registries.sync_places(runtime, registries.geo_url()))

    @router.post("/hub/zones")
    async def create_zone(request: Request) -> Any:
        body = await body_of(request)
        return await run(request, lambda: registries.create_zone(runtime, body))

    @router.patch("/hub/zones/{zone_id}")
    async def update_zone(zone_id: str, request: Request) -> Any:
        body = await body_of(request)
        return await run(request, lambda: registries.update_zone(runtime, zone_id, body))

    @router.delete("/hub/zones/{zone_id}")
    async def delete_zone(zone_id: str, request: Request) -> Any:
        return await run(request, lambda: registries.delete_zone(runtime, zone_id))

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
