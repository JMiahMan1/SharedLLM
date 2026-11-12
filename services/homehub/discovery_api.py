"""The ``/hub/discovered`` admin API: new devices on the LAN, and adding them.

| Route | What |
|---|---|
| ``GET /hub/discovered[?new=1]`` | Every finding (``new=1``: only those not set up and not ignored) |
| ``POST /hub/discovered/scan`` | Run SSDP and the WiZ sweep now (at most once a minute) |
| ``POST /hub/discovered/{id}/adopt`` | ``{"name"?, "area_id"?, "input": {...}}``; answers ``status: adopted`` or ``status: needs_input`` with ``needs`` |
| ``POST /hub/discovered/{id}/ignore`` | ``{"ignored": false}`` un-ignores |
| ``POST /hub/matter/commission`` | ``{"code", "name"?, "area_id"?}`` for a Matter device that does not announce itself |

Admins only: the stack's ``INTERNAL_SECRET`` or an HA admin token, as for the
rest of ``/hub/*``. The manager is ``runtime.discovery`` (set by main.py).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub import flows
from services.homehub.auth import Auth, bearer
from services.homehub.core import HubError

log = logging.getLogger("homehub.discovery_api")


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()

    def admin(request: Request) -> None:
        token = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(token)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")

    def manager() -> Any:
        mgr = getattr(runtime, "discovery", None)
        if mgr is None:
            raise HTTPException(503, detail="Device discovery is not running")
        return mgr

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

    def flow_failure(exc: Exception) -> JSONResponse:
        status = int(getattr(exc, "status", 400) or 400)
        return JSONResponse({"status": "unsupported" if status == 501 else "error", "message": str(exc)},
                            status_code=status)

    @router.get("/hub/discovered")
    async def discovered(request: Request, new: str | None = None) -> dict[str, Any]:
        admin(request)
        mgr = manager()
        new_only = str(new or "").lower() in ("1", "true", "yes")
        return {"devices": mgr.list(new_only=new_only), "last_scan": mgr.last_scan}

    @router.post("/hub/discovered/scan")
    async def scan(request: Request) -> dict[str, Any]:
        admin(request)
        mgr = manager()
        result = await mgr.scan(reason="on demand")
        return {"scan": result, "devices": mgr.list()}

    @router.post("/hub/discovered/{finding_id}/adopt")
    async def adopt(finding_id: str, request: Request) -> Any:
        admin(request)
        mgr = manager()
        finding = mgr.get(finding_id)
        if finding is None:
            raise HTTPException(404, detail="No such discovered device")
        body = await body_of(request)
        pub = mgr.public(finding)
        if pub["known"]:
            return JSONResponse({"status": "error", "message": f"Already set up as {pub['entry_title'] or 'an entry'}",
                                 "entry_id": pub["entry_id"]}, status_code=409)
        try:
            result = await flows.adopt(runtime, finding, body)
        except flows.NeedsInput as need:
            return {"status": "needs_input", "message": need.message, "error": need.error, "needs": need.needs}
        except HubError as exc:
            return flow_failure(exc)
        mgr.mark_adopted(finding_id, result["entry_id"])
        return result

    @router.post("/hub/discovered/{finding_id}/ignore")
    async def ignore(finding_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        body = await body_of(request)
        result = manager().set_ignored(finding_id, bool(body.get("ignored", True)))
        if result is None:
            raise HTTPException(404, detail="No such discovered device")
        return result

    @router.post("/hub/matter/commission")
    async def matter_commission(request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            return await flows.commission_matter(runtime, body.get("code"), name=body.get("name") or None,
                                                 area_id=body.get("area_id") or None)
        except HubError as exc:
            return flow_failure(exc)

    return router
