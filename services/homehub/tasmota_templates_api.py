"""The ``/hub/tasmota/...`` template API: the catalog, a device's template and suggestions, loading one.

| Route | What |
|---|---|
| ``GET /hub/tasmota/templates[?kind=&q=]`` | The catalog (``data/tasmota_templates.json``), by kind (bulb, plug, switch, dimmer, strip, relay) and words |
| ``GET /hub/tasmota/{entry_id}/template`` | ``{"current", "up_to_date", "message", "suggestions", "previous_template"}``: the device's template, module and names now, the catalog entries it equals, and up to 3 suggestions with their reasons |
| ``POST /hub/tasmota/{entry_id}/template`` | ``{"template": <json> or "catalog_id", "confirm": true}``: load it (the device restarts), verify it, reload the entry |
| ``POST /hub/tasmota/{entry_id}/template/restore`` | ``{"confirm": true}``: send back the template the last load replaced |

Admins only, like the rest of ``/hub/*``. Nothing is written to a device without ``"confirm": true``; a
malformed template is refused before anything is sent. See ``tasmota_templates.py``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub import tasmota_templates as templates
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
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        return body

    def failure(exc: HubError) -> JSONResponse:
        status = int(getattr(exc, "status", 400) or 400)
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=status)

    @router.get("/hub/tasmota/templates")
    async def catalog(request: Request, kind: str | None = None, q: str | None = None) -> dict[str, Any]:
        admin(request)
        if kind and kind not in templates.KINDS:
            raise HTTPException(400, detail="kind is one of " + ", ".join(templates.KINDS))
        return {"kinds": list(templates.KINDS), "templates": templates.search_catalog(kind, q)}

    @router.get("/hub/tasmota/{entry_id}/template")
    async def current(entry_id: str, request: Request) -> Any:
        admin(request)
        try:
            return await templates.read_device(runtime, entry_id)
        except HubError as exc:
            return failure(exc)

    @router.post("/hub/tasmota/{entry_id}/template")
    async def apply(entry_id: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            return await templates.apply_template(runtime, entry_id, body)
        except HubError as exc:
            return failure(exc)

    @router.post("/hub/tasmota/{entry_id}/template/restore")
    async def restore(entry_id: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            return await templates.restore_previous(runtime, entry_id, body)
        except HubError as exc:
            return failure(exc)

    return router
