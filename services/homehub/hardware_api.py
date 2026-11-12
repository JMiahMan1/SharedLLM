"""The hardware list's admin API (``hardware.py``): integrations, devices, entities.

| Route | What |
|---|---|
| ``GET /hub/integrations`` | One row per integration: entries with state and error, counts, how it is discovered and added |
| ``GET /hub/hardware`` | Every device from the live registry (``hardware.device_view``), plus the areas |
| ``GET /hub/hardware/{device_id}`` | One device with its entities, their states and registry settings |
| ``POST /hub/hardware/{device_id}/disable`` | ``{"disabled": bool}``; its entities follow (``disabled_by: device``) |
| ``PATCH /hub/entities/{entity_id}`` | ``{"name"?, "area_id"?, "hidden"?, "disabled"?}`` |
| ``POST /hub/entries/{id}/disable`` | ``{"disabled": bool}``: unload or start the entry |
| ``POST /hub/entries/{id}/sync`` | Register the devices a loaded driver found that the registry lacks |
| ``POST /hub/discovered/{id}/update-address`` | A known device found at a new address: move its entry there |

Reload is ``POST /hub/entries/{id}/reload`` (``api.py``). Admins only, like the rest of ``/hub/*``.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub import hardware
from services.homehub.auth import Auth, bearer
from services.homehub.core import HubError

_ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


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
            body = {}
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        return body

    def failure(exc: HubError) -> JSONResponse:
        status = int(getattr(exc, "status", 400) or 400)
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=status)

    def flag(body: dict[str, Any]) -> bool:
        if "disabled" not in body or not isinstance(body["disabled"], bool):
            raise HTTPException(400, detail="disabled must be true or false")
        return body["disabled"]

    @router.get("/hub/integrations")
    async def integrations(request: Request) -> dict[str, Any]:
        admin(request)
        return hardware.integrations(runtime)

    @router.get("/hub/hardware")
    async def devices(request: Request) -> dict[str, Any]:
        admin(request)
        return hardware.device_list(runtime)

    @router.get("/hub/hardware/{device_id}")
    async def device(device_id: str, request: Request) -> Any:
        admin(request)
        try:
            return hardware.device_detail(runtime, device_id)
        except HubError as exc:
            return failure(exc)

    @router.post("/hub/hardware/{device_id}/disable")
    async def disable_device(device_id: str, request: Request) -> Any:
        admin(request)
        disabled = flag(await body_of(request))
        try:
            return await hardware.set_device_disabled(runtime, device_id, disabled)
        except HubError as exc:
            return failure(exc)

    @router.patch("/hub/entities/{entity_id}")
    async def update_entity(entity_id: str, request: Request) -> Any:
        admin(request)
        if not _ENTITY_ID.match(entity_id):
            raise HTTPException(404, detail="No such entity")
        body = await body_of(request)
        try:
            return await hardware.update_entity(runtime, entity_id, body)
        except HubError as exc:
            return failure(exc)

    @router.post("/hub/entries/{entry_id}/disable")
    async def disable_entry(entry_id: str, request: Request) -> Any:
        admin(request)
        disabled = flag(await body_of(request))
        try:
            return await hardware.set_entry_disabled(runtime, entry_id, disabled)
        except HubError as exc:
            return failure(exc)

    @router.post("/hub/entries/{entry_id}/sync")
    async def sync(entry_id: str, request: Request) -> Any:
        admin(request)
        try:
            return await hardware.sync_entities(runtime, entry_id)
        except HubError as exc:
            return failure(exc)

    @router.post("/hub/discovered/{finding_id}/update-address")
    async def update_address(finding_id: str, request: Request) -> Any:
        admin(request)
        try:
            return await hardware.update_address(runtime, finding_id)
        except HubError as exc:
            return failure(exc)

    return router
