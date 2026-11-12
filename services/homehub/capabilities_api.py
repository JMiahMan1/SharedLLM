"""The capability index over HTTP (``capabilities.py``), for every service that picks devices.

| Route | What |
|---|---|
| ``GET /api/homehub/capabilities`` | ``{"abilities": {ability: meaning}, "counts": {ability: n}, "areas": [{area_id, name, aliases}], "entities": [...]}``. Filters: ``ability`` (repeatable; all must hold), ``area`` (id or name), ``domain``, ``device_id``, ``platform``, ``available=1`` |
| ``GET /api/homehub/capabilities/{entity_id}`` | One entity's abilities, services, details and protocol |
| ``GET /api/homehub/capabilities/devices/{device_id}`` | A device's abilities and which entity provides each |
| ``GET /api/homehub/capabilities/provenance`` | How many entities and devices the devices themselves confirmed, and which hold only imported data |

Any token the hub accepts (an HA token or the internal secret): the answers
name entities and what they can do, as ``/api/states`` already does. Who may
control what is decided by the caller (execution's entity permissions).
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub import capabilities
from services.homehub.auth import Auth, bearer

_ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()
    hub = runtime.hub

    def signed_in(request: Request) -> None:
        tok = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        if auth.authenticate(tok) is None:
            raise HTTPException(401, detail="Unauthorized")

    @router.get("/api/homehub/capabilities")
    async def index(request: Request) -> Any:
        signed_in(request)
        q = request.query_params
        try:
            found = capabilities.find(
                hub, q.getlist("ability"), area=q.get("area") or None, domain=q.get("domain") or None,
                device_id=q.get("device_id") or None, platform=q.get("platform") or None,
                available_only=str(q.get("available") or "").lower() in ("1", "true", "yes"))
        except ValueError as exc:
            return JSONResponse({"message": str(exc)}, status_code=400)
        except LookupError as exc:
            return JSONResponse({"message": str(exc)}, status_code=404)
        filtered = any(q.get(k) for k in ("ability", "area", "domain", "device_id", "platform", "available"))
        return {"abilities": capabilities.ABILITIES,
                "counts": capabilities.summary(hub) if not filtered else None,
                "areas": capabilities.areas(hub), "entities": found}

    @router.get("/api/homehub/capabilities/provenance")
    async def provenance(request: Request) -> Any:
        signed_in(request)
        return capabilities.provenance(hub)

    @router.get("/api/homehub/capabilities/devices/{device_id}")
    async def device(device_id: str, request: Request) -> Any:
        signed_in(request)
        if device_id not in hub.devices:
            return JSONResponse({"message": "No such device"}, status_code=404)
        return {"device_id": device_id, **capabilities.device_capabilities(hub, device_id)}

    @router.get("/api/homehub/capabilities/{entity_id}")
    async def entity(entity_id: str, request: Request) -> Any:
        signed_in(request)
        if not _ENTITY_ID.match(entity_id) or (entity_id not in hub.entities and hub.states.get(entity_id) is None):
            return JSONResponse({"message": "No such entity"}, status_code=404)
        return capabilities.entity_capabilities(hub, entity_id)

    return router
