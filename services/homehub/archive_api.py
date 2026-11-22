"""Admin routes for archived devices (``device_archive.py``).

| Route | What |
|---|---|
| ``GET /hub/archive`` | ``{"settings", "items", "audit", "last_delete_run"}``: every archived entry and device with its last contact, when it was archived and when it will be deleted |
| ``GET /hub/archive/preview`` | Dry run: ``{"archive": [...], "delete": [...]}``, what the next sweep and the next deletion pass would do (with the history and statistics rows a deletion removes). Changes nothing |
| ``PUT /hub/archive/settings`` | ``{"enabled"?, "archive_after_days"?, "delete_after_days"?}`` |
| ``POST /hub/archive/restore`` | ``{"kind": "entry"|"device", "id"}``: back as it was, set up again |
| ``POST /hub/archive/delete`` | ``{"kind", "id"}``: delete one archived item now (refused for anything not archived) |

Admins only, like the rest of ``/hub/*``. 503 while the archive is not running (the hub is starting).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub.auth import Auth, bearer
from services.homehub.core import HubError

KINDS = ("entry", "device")


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()

    def admin(request: Request) -> Any:
        tok = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(tok)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")
        archive = getattr(runtime, "archive", None)
        if archive is None:
            raise HTTPException(503, detail="The device archive is not running yet")
        return archive

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        return body

    def target(body: dict[str, Any]) -> tuple[str, str]:
        kind, item_id = body.get("kind"), body.get("id")
        if kind not in KINDS or not isinstance(item_id, str) or not item_id:
            raise HTTPException(400, detail='Say what to act on: {"kind": "entry" or "device", "id"}')
        return kind, item_id

    def failure(exc: HubError) -> JSONResponse:
        status = int(getattr(exc, "status", 400) or 400)
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=status)

    @router.get("/hub/archive")
    async def listing(request: Request) -> dict[str, Any]:
        return admin(request).listing()

    @router.get("/hub/archive/preview")
    async def preview(request: Request) -> dict[str, Any]:
        return admin(request).preview()

    @router.put("/hub/archive/settings")
    async def settings(request: Request) -> Any:
        archive = admin(request)
        try:
            return {"settings": archive.update_settings(await body_of(request))}
        except HubError as exc:
            return failure(exc)

    @router.post("/hub/archive/restore")
    async def restore(request: Request) -> Any:
        archive = admin(request)
        kind, item_id = target(await body_of(request))
        try:
            return archive.restore(kind, item_id, why="admin")
        except HubError as exc:
            return failure(exc)

    @router.post("/hub/archive/delete")
    async def delete(request: Request) -> Any:
        archive = admin(request)
        kind, item_id = target(await body_of(request))
        try:
            return await archive.delete(kind, item_id, why="admin")
        except HubError as exc:
            return failure(exc)

    return router
