"""Admin routes for HA's helpers (``helpers.py``).

| Route | What |
|---|---|
| ``GET /hub/helpers`` | every helper (config, entity id, state), the types with their fields, and what was left out |
| ``POST /hub/helpers/{domain}`` | create one from its configuration (HA's storage item shape) |
| ``PUT /hub/helpers/{domain}/{id}`` | change one |
| ``DELETE /hub/helpers/{domain}/{id}`` | remove one |
| ``POST /hub/helpers/{domain}/{id}/call`` | ``{"service", "data"?}``: run one of its services (turn on, start, set...) |

Admins only, like the rest of ``/hub/*``. A change reloads the automations,
so an item that named a helper that did not exist yet loads now (and one that
names a helper just deleted says so).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub.auth import Auth, bearer
from services.homehub.core import HubError
from services.homehub.helpers import DOMAINS, FIELDS, LABELS, SERVICES, HelperError, Helpers

log = logging.getLogger("homehub.helpers_api")


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()

    def admin(request: Request) -> None:
        tok = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(tok)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")

    def manager() -> Helpers:
        m = getattr(runtime, "helpers", None)
        if m is None:
            raise HTTPException(503, detail="Helpers are still starting")
        return m

    def domain_of(domain: str) -> str:
        if domain not in DOMAINS:
            raise HTTPException(404, detail=f"Unknown helper type {domain}")
        return domain

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400, detail="The body must be a JSON object") from None
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        return body

    def failure(exc: HubError) -> JSONResponse:
        return JSONResponse({"message": str(exc)}, status_code=int(getattr(exc, "status", 400)))

    async def reload_automations() -> None:
        engine = getattr(runtime, "automations", None)
        if engine is not None:
            try:
                await engine.reload()
            except Exception:
                log.exception("automations did not reload after a helper change")

    def one(m: Helpers, domain: str, hid: str) -> dict[str, Any]:
        h = m.get(domain, hid)
        return next(r for r in m.list() if r["entity_id"] == h.entity_id)

    @router.get("/hub/helpers")
    async def list_helpers(request: Request) -> dict[str, Any]:
        admin(request)
        m = manager()
        return {
            "helpers": m.list(),
            "types": [{"domain": d, "label": LABELS[d], "fields": FIELDS[d], "services": sorted(SERVICES[d])}
                      for d in DOMAINS],
            "problems": list(m.problems),
        }

    @router.post("/hub/helpers/{domain}")
    async def create(domain: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        m = manager()
        try:
            h = await m.create(domain_of(domain), body)
        except HelperError as exc:
            return failure(exc)
        await reload_automations()
        return one(m, domain, h.id)

    @router.put("/hub/helpers/{domain}/{helper_id}")
    async def update(domain: str, helper_id: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        m = manager()
        try:
            await m.update(domain_of(domain), helper_id, body)
        except HelperError as exc:
            return failure(exc)
        await reload_automations()
        return one(m, domain, helper_id)

    @router.delete("/hub/helpers/{domain}/{helper_id}")
    async def delete(domain: str, helper_id: str, request: Request) -> Any:
        admin(request)
        try:
            await manager().delete(domain_of(domain), helper_id)
        except HelperError as exc:
            return failure(exc)
        await reload_automations()
        return {"deleted": f"{domain}.{helper_id}"}

    @router.post("/hub/helpers/{domain}/{helper_id}/call")
    async def call(domain: str, helper_id: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        m = manager()
        try:
            h = m.get(domain_of(domain), helper_id)
            service = str(body.get("service") or "")
            if service not in SERVICES[domain]:
                raise HelperError(f"{domain} has no service {service or '(none given)'}")
            data = body.get("data") or {}
            if not isinstance(data, dict):
                raise HelperError("data must be an object")
            await runtime.hub.call_service(domain, service, {**data, "entity_id": h.entity_id})
        except HubError as exc:
            return failure(exc)
        return one(m, domain, helper_id)

    return router
