"""The ``/hub/bluetooth`` admin API: scanners, devices heard, and the room map.

| Route | What |
|---|---|
| ``GET /hub/bluetooth`` | The host adapter (configured, resolved, scan mode and why, or why it is off), every scanner (area, ``ref_power``, ``rssi_offset``, online, advertisements and rate per minute, last seen, error), trackers, parser libraries |
| ``GET /hub/bluetooth/devices?seconds=300`` | Devices heard recently: address, name, RSSI per scanner, parser match, iBeacon id, entry |
| ``PATCH /hub/bluetooth/scanners/{id}`` | ``{"area_id"?, "ref_power"?, "rssi_offset"?}``; ``null`` clears one |
| ``POST /hub/bluetooth/map/sessions`` | ``{"area_id", "tracker", "duration_s"? (30-300, default 60)}`` -> ``{session_id, area_id, tracker, ends_at}``; 409 when the tracker was not heard in the last 30 s, 404 for an unknown area or tracker |
| ``GET /hub/bluetooth/map/sessions/{id}`` | ``{status: recording|done|failed, samples, scanners_heard, seconds_left, error?}`` |
| ``DELETE /hub/bluetooth/map/sessions/{id}`` | Cancel; nothing of it is kept |
| ``GET /hub/bluetooth/map`` | ``{method, areas: [{area_id, name, samples, sessions, last_recorded, accuracy}], scanners, confusion, overall_accuracy, stale_reason}`` |
| ``DELETE /hub/bluetooth/map/{area_id}`` | Clear one room's fingerprints |
| ``DELETE /hub/bluetooth/map?confirm=1`` | Clear every room's fingerprints |

Admins only (the internal secret or an HA admin token), as the rest of ``/hub/*``.
The manager is ``hub.bluetooth`` (set by main.py).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.homehub.auth import Auth, bearer
from services.homehub.bluetooth import BluetoothError


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()

    def admin(request: Request) -> None:
        token = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(token)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")

    def manager() -> Any:
        mgr = getattr(runtime.hub, "bluetooth", None)
        if mgr is None:
            raise HTTPException(503, detail="The hub's Bluetooth platform is not running")
        return mgr

    async def body_of(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        return body

    def failed(exc: BluetoothError) -> JSONResponse:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=exc.status)

    @router.get("/hub/bluetooth")
    async def status(request: Request) -> dict[str, Any]:
        admin(request)
        return manager().status()

    @router.get("/hub/bluetooth/devices")
    async def devices(request: Request, seconds: float = 300.0) -> dict[str, Any]:
        admin(request)
        if not 1 <= seconds <= 3600:
            raise HTTPException(400, detail="seconds must be 1 to 3600")
        return {"devices": manager().devices_view(seconds)}

    @router.patch("/hub/bluetooth/scanners/{scanner_id}")
    async def patch_scanner(scanner_id: str, request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            return manager().set_scanner(scanner_id, body)
        except BluetoothError as exc:
            return failed(exc)

    @router.post("/hub/bluetooth/map/sessions")
    async def start_session(request: Request) -> Any:
        admin(request)
        body = await body_of(request)
        try:
            return manager().start_session(body.get("area_id"), body.get("tracker"), body.get("duration_s"))
        except BluetoothError as exc:
            return failed(exc)

    @router.get("/hub/bluetooth/map/sessions/{session_id}")
    async def get_session(session_id: str, request: Request) -> Any:
        admin(request)
        try:
            return manager().session(session_id)
        except BluetoothError as exc:
            return failed(exc)

    @router.delete("/hub/bluetooth/map/sessions/{session_id}")
    async def cancel_session(session_id: str, request: Request) -> Any:
        admin(request)
        try:
            manager().cancel_session(session_id)
        except BluetoothError as exc:
            return failed(exc)
        return {"status": "cancelled", "session_id": session_id}

    @router.get("/hub/bluetooth/map")
    async def get_map(request: Request) -> dict[str, Any]:
        admin(request)
        return manager().map_view()

    @router.delete("/hub/bluetooth/map/{area_id}")
    async def clear_area(area_id: str, request: Request) -> Any:
        admin(request)
        try:
            return {"status": "cleared", "area_id": area_id, "sessions": manager().clear_map(area_id)}
        except BluetoothError as exc:
            return failed(exc)

    @router.delete("/hub/bluetooth/map")
    async def clear_all(request: Request, confirm: str | None = None) -> Any:
        admin(request)
        if str(confirm or "").lower() not in ("1", "true", "yes"):
            return JSONResponse({"status": "error", "message": "Clearing every room's fingerprints needs ?confirm=1"},
                                status_code=400)
        return {"status": "cleared", "sessions": manager().clear_map()}

    return router
