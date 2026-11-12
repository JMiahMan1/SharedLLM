"""The ``/hub/entries``, ``/hub/devices`` and ``/hub/flows`` admin API: what is set up, and adding by address.

| Route | What |
|---|---|
| ``GET /hub/entries`` | Every integration entry with its devices, entities and reconfigurable fields, plus the areas |
| ``PATCH /hub/entries/{id}`` | ``{"title"?, "data"?: {"host"?, "url"?, "username"?, "password"?, ...}}``; reloads it |
| ``DELETE /hub/entries/{id}[?force=1]`` | Unpair (HomeKit), remove the entry, its entities and devices |
| ``PATCH /hub/devices/{id}`` | ``{"name_by_user"?, "area_id"?}`` |
| ``DELETE /hub/devices/{id}[?force=1]`` | Remove one device (its entry too when it was the entry's only device) |
| ``GET /hub/flows`` | Integrations that can be added by address, with their fields |
| ``POST /hub/flows/{domain}`` | ``{"host"? or "url"?, "name"?, "area_id"?, "input"?}``; same answers as adopting a finding |
| ``GET /hub/esphome-builder`` | ``{"configured", "url", "state"}`` (never the login) |

Admins only, like the rest of ``/hub/*``. See ``entries.py`` and ``pairing.py``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub import entries, flows, pairing
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

    def truthy(v: str | None) -> bool:
        return str(v or "").lower() in ("1", "true", "yes")

    @router.get("/hub/entries")
    async def list_entries(request: Request) -> dict[str, Any]:
        admin(request)
        return entries.list_entries(runtime)

    @router.patch("/hub/entries/{entry_id}")
    async def reconfigure(entry_id: str, request: Request) -> Any:
        admin(request)
        try:
            return await entries.reconfigure_entry(runtime, entry_id, await body_of(request))
        except HubError as exc:
            return failure(exc)

    @router.delete("/hub/entries/{entry_id}")
    async def remove_entry(entry_id: str, request: Request, force: str | None = None) -> Any:
        admin(request)
        try:
            return await entries.remove_entry(runtime, entry_id, force=truthy(force))
        except HubError as exc:
            return failure(exc)

    @router.patch("/hub/devices/{device_id}")
    async def update_device(device_id: str, request: Request) -> Any:
        admin(request)
        try:
            return entries.update_device(runtime, device_id, await body_of(request))
        except HubError as exc:
            return failure(exc)

    @router.delete("/hub/devices/{device_id}")
    async def remove_device(device_id: str, request: Request, force: str | None = None) -> Any:
        admin(request)
        try:
            return await entries.remove_device(runtime, device_id, force=truthy(force))
        except HubError as exc:
            return failure(exc)

    @router.get("/hub/flows")
    async def manual_flows(request: Request) -> dict[str, Any]:
        admin(request)
        return {"flows": [{"domain": d, **spec} for d, spec in pairing.MANUAL.items()]}

    @router.post("/hub/flows/{domain}")
    async def manual_add(domain: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            finding = pairing.manual_finding(domain, body)
        except HubError as exc:
            status = int(getattr(exc, "status", 400) or 400)
            return JSONResponse({"status": "unsupported" if status == 501 else "error", "message": str(exc)},
                                status_code=status)
        status, result = await flows.adopt_response(runtime, finding, body)
        return JSONResponse(result, status_code=status) if status != 200 else result

    @router.get("/hub/esphome-builder")
    async def esphome_builder(request: Request) -> dict[str, Any]:
        admin(request)
        return entries.esphome_builder(runtime)

    return router
