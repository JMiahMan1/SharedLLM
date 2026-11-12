"""Admin routes for pruning stale imported HA data (``prune.py``).

| Route | What |
|---|---|
| ``GET /hub/prune/plan?days=365&drop_history=0`` | Dry run: what would be removed and why, what was kept and why, ``plan_id`` and ``cutoff``. Changes nothing |
| ``POST /hub/prune/apply`` | ``{"plan_id", "cutoff", "drop_history"?}`` from a dry run: removes exactly that plan, 409 when the hub changed since |

Admins only, like the rest of ``/hub/*``. HA's registry timestamps are read
(read-only) from ``$HOMEHUB_IMPORT_DIR/homeassistant`` when it is there.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub import prune
from services.homehub.auth import Auth, bearer
from services.homehub.core import HubError

IMPORT_DIR = os.getenv("HOMEHUB_IMPORT_DIR", "/import")


def ha_config_dir() -> str:
    return os.path.join(IMPORT_DIR, "homeassistant")


def _flag(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("1", "true", "yes", "0", "false", "no", ""):
        return value.lower() in ("1", "true", "yes")
    raise HTTPException(400, detail=f"{name} must be true or false")


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()

    def admin(request: Request) -> None:
        tok = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(tok)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")

    def failure(exc: HubError) -> JSONResponse:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=int(getattr(exc, "status", 400)))

    @router.get("/hub/prune/plan")
    async def plan(request: Request, days: str = str(prune.DEFAULT_DAYS), drop_history: str = "0") -> Any:
        admin(request)
        if not days.isdigit():
            raise HTTPException(400, detail="days must be a whole number")
        try:
            cutoff = prune.cutoff_for(int(days))
            snap = prune.snapshot_from_runtime(runtime, ha_config_dir())
            return {"days": int(days), **prune.build_plan(snap, cutoff, drop_history=_flag(drop_history,
                                                                                           "drop_history"))}
        except HubError as exc:
            return failure(exc)

    @router.post("/hub/prune/apply")
    async def apply(request: Request) -> Any:
        admin(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object with plan_id and cutoff")
        try:
            return await prune.apply_plan(runtime, plan_id=body.get("plan_id"), cutoff=body.get("cutoff"),
                                          drop_history=_flag(body.get("drop_history", False), "drop_history"),
                                          import_dir=ha_config_dir())
        except HubError as exc:
            return failure(exc)

    return router
