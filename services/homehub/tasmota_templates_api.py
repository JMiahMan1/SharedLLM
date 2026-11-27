"""The ``/hub/tasmota/...`` template API: the catalog, a device's template and suggestions, loading one.

| Route | What |
|---|---|
| ``GET /hub/tasmota/templates[?kind=&q=]`` | The catalog (``data/tasmota_templates.json``), by kind (bulb, plug, switch, dimmer, strip, relay) and words, and ``measured``: the templates measured in this household |
| ``GET /hub/tasmota/{entry_id}/template`` | ``{"current", "up_to_date", "message", "identified", "identify_note", "suggestions", "guesses", "previous_template"}``: the device's template, module and names now, the templates it equals, and up to 3 suggestions with their reasons (``guesses`` instead when nothing identifies the device) |
| ``POST /hub/tasmota/{entry_id}/template`` | ``{"template": <json>, "catalog_id" or "measured_id", "confirm": true}``: load it (the device restarts), verify it, reload the entry |
| ``POST /hub/tasmota/{entry_id}/template/restore`` | ``{"confirm": true}``: send back the template the last load replaced |
| ``POST /hub/tasmota/{entry_id}/measured`` | ``{"manufacturer", "model", "kind"?, "notes"?}``: save the template the device runs now as measured on it (reads the device only) |
| ``POST /hub/tasmota/measured`` | ``{"entry_id" or "device_name", "manufacturer", "model", "kind", "template", "commands"?, "how"?, "notes"?, "measured_at"?}``: record a template worked out by hand; sends nothing to the device |
| ``DELETE /hub/tasmota/measured/{id}`` | Forget a measured template |
| ``GET /hub/tasmota/{entry_id}/pinfinder`` | The pin finder's state on the device: the question to answer, its result, or a run to put back |
| ``POST /hub/tasmota/{entry_id}/pinfinder/start`` | ``{"confirm": true, "manufacturer"?, "model"?, "kind"?}``: save the template, load the test template, ask the first question |
| ``POST /hub/tasmota/{entry_id}/pinfinder/answer`` | ``{"step", "answer"}`` (``"again"`` shows the last test once more) |
| ``POST /hub/tasmota/{entry_id}/pinfinder/cancel`` | ``{"confirm": true}``: stop and put the saved template back |

Admins only, like the rest of ``/hub/*``. Nothing is written to a device without ``"confirm": true``; a
malformed template is refused before anything is sent. See ``tasmota_templates.py``, ``tasmota_measured.py``
and ``tasmota_pin_finder.py``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub import tasmota_measured as measured
from services.homehub import tasmota_pin_finder as pin_finder
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
        return {"kinds": list(templates.KINDS), "templates": templates.search_catalog(kind, q),
                "measured": measured.search(runtime, kind, q)}

    @router.post("/hub/tasmota/measured")
    async def record_measured(request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            return await measured.save_given(runtime, body)
        except HubError as exc:
            return failure(exc)

    @router.delete("/hub/tasmota/measured/{rec_id}")
    async def forget_measured(rec_id: str, request: Request) -> Any:
        admin(request)
        try:
            measured.delete(runtime, rec_id)
        except HubError as exc:
            return failure(exc)
        return {"status": "deleted", "id": rec_id}

    @router.post("/hub/tasmota/{entry_id}/measured")
    async def save_measured(entry_id: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            return await measured.save_from_device(runtime, entry_id, body)
        except HubError as exc:
            return failure(exc)

    @router.get("/hub/tasmota/{entry_id}/pinfinder")
    async def finder_state(entry_id: str, request: Request) -> Any:
        admin(request)
        try:
            return pin_finder.state(runtime, entry_id)
        except HubError as exc:
            return failure(exc)

    @router.post("/hub/tasmota/{entry_id}/pinfinder/{action}")
    async def finder_action(entry_id: str, action: str, request: Request) -> Any:
        admin(request)
        handlers = {"start": pin_finder.start, "answer": pin_finder.answer, "cancel": pin_finder.cancel}
        if action not in handlers:
            raise HTTPException(404, detail="Not found")
        body = await body_of(request)
        try:
            return await handlers[action](runtime, entry_id, body)
        except HubError as exc:
            return failure(exc)

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
