"""The ``/hub/light_groups`` API: light groups (``light_groups.py``, driven by ``drivers/group.py``).

| Route | What |
|---|---|
| ``GET /hub/light_groups`` | ``{"groups", "lights", "areas"}``: each group with its members, the lights that can join, the rooms |
| ``POST /hub/light_groups`` | ``{"name", "entities": [light ids], "area_id"?}`` makes one |
| ``PATCH /hub/light_groups/{entry_id}`` | any of those keys changes it |
| ``DELETE /hub/light_groups/{entry_id}`` | removes it (its lights are untouched) |

Admins only, like the rest of ``/hub/*``: the gateway lets parents in
(``services/gateway/light_group_routes.py``) and calls with the internal secret.
A change reloads the automations, so one that names a group made just now loads.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub import light_groups
from services.homehub.auth import Auth, bearer
from services.homehub.core import HubError

log = logging.getLogger("homehub.light_groups_api")


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()

    def admin(request: Request) -> None:
        tok = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(tok)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, detail="The body must be a JSON object") from None
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        return body

    def failure(exc: HubError) -> JSONResponse:
        status = int(getattr(exc, "status", 400) or 400)
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=status)

    async def reload_automations() -> None:
        engine = getattr(runtime, "automations", None)
        if engine is not None:
            try:
                await engine.reload()
            except Exception:
                log.exception("automations did not reload after a light group change")

    @router.get("/hub/light_groups")
    async def list_groups(request: Request) -> dict[str, Any]:
        admin(request)
        return light_groups.list_groups(runtime)

    @router.post("/hub/light_groups")
    async def create(request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            out = await light_groups.create_group(runtime, body)
        except HubError as exc:
            return failure(exc)
        await reload_automations()
        return out

    @router.patch("/hub/light_groups/{entry_id}")
    async def update(entry_id: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            out = await light_groups.update_group(runtime, entry_id, body)
        except HubError as exc:
            return failure(exc)
        await reload_automations()
        return out

    @router.delete("/hub/light_groups/{entry_id}")
    async def delete(entry_id: str, request: Request) -> Any:
        admin(request)
        try:
            out = await light_groups.delete_group(runtime, entry_id)
        except HubError as exc:
            return failure(exc)
        await reload_automations()
        return out

    return router
