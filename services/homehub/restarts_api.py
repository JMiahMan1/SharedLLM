"""The ``/hub/devices/{id}/restarts`` admin API: an ESPHome node's restarts and drops (``esphome_restarts.py``).

| Route | What |
|---|---|
| ``GET /hub/devices/{id}/restarts`` | ``{"supported", "device_id", ...}``: the node's recent restarts and drops (newest first) with kind, downtime, reset reason, cause, what the hub was doing and the log lines around each; the log level and buffer; the diagnostic sensors it has, and the YAML to add when it lacks them. ``supported: false`` for a device that is not an ESPHome node |
| ``PATCH /hub/devices/{id}/restarts`` | ``{"log_level": "off"/"error"/"warning"/"info"/"debug"}``: the level of the node's log lines the hub keeps (entry option ``restart_log_level``), applied on the live connection |

Admins only, like the rest of ``/hub/*``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.homehub import esphome_restarts
from services.homehub.auth import Auth, bearer


def monitor_for(hub: Any, device_id: str) -> tuple[Any, Any]:
    """``(device, monitor)``; the monitor is None when no ESPHome driver serves the device."""
    device = hub.devices.get(device_id)
    if device is None:
        raise HTTPException(404, detail="Device not found")
    for entry_id in getattr(device, "config_entries", None) or []:
        mon = getattr(hub.drivers.get(entry_id), "restarts", None)
        if isinstance(mon, esphome_restarts.RestartMonitor):
            return device, mon
    return device, None


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()
    hub = runtime.hub

    def admin(request: Request) -> None:
        tok = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(tok)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")

    @router.get("/hub/devices/{device_id}/restarts")
    async def restarts(device_id: str, request: Request, lines: int = 50) -> dict[str, Any]:
        admin(request)
        _device, mon = monitor_for(hub, device_id)
        if mon is None:
            return {"supported": False, "device_id": device_id}
        return {"supported": True, "device_id": device_id, **mon.public(lines=max(0, min(lines, 200)))}

    @router.patch("/hub/devices/{device_id}/restarts")
    async def set_level(device_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        level = body.get("log_level") if isinstance(body, dict) else None
        if level not in esphome_restarts.LOG_LEVELS:
            raise HTTPException(400, detail="The body must be {\"log_level\": one of "
                                + ", ".join(esphome_restarts.LOG_LEVELS) + "}")
        _device, mon = monitor_for(hub, device_id)
        if mon is None:
            raise HTTPException(409, detail="This device is not an ESPHome node")
        entry = mon.driver.entry
        if level == esphome_restarts.DEFAULT_LOG_LEVEL:
            entry.options.pop(esphome_restarts.OPTION, None)
        else:
            entry.options[esphome_restarts.OPTION] = level
        runtime.store.save_config_entry(entry)
        mon.set_log_level(level)
        return {"supported": True, "device_id": device_id, **mon.public()}

    return router
