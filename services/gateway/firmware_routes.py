"""Gateway routes for the Firmware page: every device's version, and installing an offered update."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from services.gateway import automation_routes

log = logging.getLogger("gateway.firmware")

router = APIRouter(prefix="/api/firmware")


@router.get("")
async def firmware_list(request: Request) -> Any:
    await automation_routes._admin(request)
    status, body = await automation_routes._hub("GET", "/hub/firmware")
    return automation_routes._answer(status, body)


@router.post("/install")
async def firmware_install(request: Request) -> Any:
    await automation_routes._admin(request)
    body = await automation_routes._json(request)
    status, answer = await automation_routes._hub("POST", "/hub/firmware/install", json=body)
    return automation_routes._answer(status, answer)


@router.post("/{device_id}/upgrade")
async def firmware_upgrade(device_id: str, request: Request) -> Any:
    await automation_routes._admin(request)
    status, body = await automation_routes._hub("POST", f"/hub/firmware/{device_id}/upgrade")
    return automation_routes._answer(status, body)
