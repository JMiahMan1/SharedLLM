"""Gateway routes for Echo OTA status and update-all."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from services.gateway import automation_routes

log = logging.getLogger("gateway.echo_ota")

router = APIRouter(prefix="/api/homehub/echo_ota")


@router.get("/status")
async def echo_ota_status(request: Request):
    await automation_routes._admin(request)
    status, body = await automation_routes._hub("GET", "/hub/echo_ota/status")
    return automation_routes._answer(status, body)


@router.post("/update_all")
async def echo_ota_update_all(request: Request):
    await automation_routes._admin(request)
    status, body = await automation_routes._hub("POST", "/hub/echo_ota/update_all")
    return automation_routes._answer(status, body)
