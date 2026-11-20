"""The ``/hub/devices/{id}/logs`` admin API: an ESPHome node's live logs (``esphome_logs.py``).

| Route | What |
|---|---|
| ``GET /hub/devices/{id}/logs?since=&level=&limit=&format=`` | ``{"supported", "device_id", "connected", "lines": [...], "seq", ...}``: the kept lines and markers after sequence number ``since``, at or above ``level`` (``error``, ``warning``, ``info``, ``debug``, ``verbose``), the newest ``limit``. ``format=text`` answers them as a plain-text download. ``supported: false`` for a device that is not an ESPHome node |
| ``GET /hub/devices/{id}/logs/stream?since=&level=`` | NDJSON: ``hello``, the kept lines after ``since``, then each new line or marker as it arrives, a ``ping`` every 15 s and an ``end`` after :data:`~services.homehub.esphome_logs.STREAM_MAX_S` (the reader reopens it from the last ``seq``). While it is open the node is asked for at least ``level`` |
| ``POST /hub/devices/{id}/logs/dump_config`` | Ask the node to log its configuration again; 409 while it is offline |

WebSocket (``WS_COMMANDS``): ``homehub/esphome/logs/subscribe`` ``{device_id, level?, since?}`` sends the same items
as events until ``unsubscribe_events``; ``homehub/esphome/logs/dump_config`` ``{device_id}``.

Admins only (a hub admin's token or the internal secret): 401 without a valid token, 403 for anyone else.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from services.homehub import esphome_logs
from services.homehub.auth import Auth, bearer
from services.homehub.restarts_api import monitor_for

_NO_STORE = {"Cache-Control": "no-store"}
_STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _level(raw: Any) -> str:
    level = esphome_logs.view_level(raw)
    if level is None:
        raise HTTPException(400, detail="level must be one of " + ", ".join(esphome_logs.VIEW_LEVELS))
    return level


def _hello(device_id: str, mon: Any) -> dict[str, Any]:
    return {"device_id": device_id, "title": mon.title, "connected": mon._client is not None,
            "levels": list(esphome_logs.VIEW_LEVELS)}


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()
    hub = runtime.hub

    def admin(request: Request) -> None:
        tok = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(tok)
        if p is None:
            raise HTTPException(401, detail="Unauthorized")
        if not p.is_admin:
            raise HTTPException(403, detail="Only an admin can read a device's logs")

    @router.get("/hub/devices/{device_id}/logs")
    async def logs(device_id: str, request: Request, since: int | None = None, level: str | None = None,
                   limit: int = 500, format: str = "json") -> Any:
        admin(request)
        lvl = _level(level)
        _device, mon = monitor_for(hub, device_id)
        if mon is None:
            return {"supported": False, "device_id": device_id}
        rows = mon.logs.history(since, lvl, max(0, min(limit, esphome_logs.BUFFER_LINES)))
        if format == "text":
            body = "\n".join(esphome_logs.text_line(r) for r in rows) + ("\n" if rows else "")
            return PlainTextResponse(body, headers={
                **_NO_STORE, "Content-Disposition": 'attachment; filename="device-log.txt"'})
        return {"supported": True, **_hello(device_id, mon), "level": lvl, "lines": rows,
                "configured_level": mon.log_level, "subscribed_level": mon.subscribed_level,
                "subscribe_error": mon.subscribe_error, **mon.logs.status()}

    @router.get("/hub/devices/{device_id}/logs/stream")
    async def stream(device_id: str, request: Request, since: int | None = None, level: str | None = None,
                     max_s: float | None = None) -> StreamingResponse:
        admin(request)
        lvl = _level(level)
        _device, mon = monitor_for(hub, device_id)
        if mon is None:
            raise HTTPException(409, detail="This device is not an ESPHome node")
        hold = esphome_logs.STREAM_MAX_S if max_s is None else max(0.05, min(float(max_s), esphome_logs.STREAM_MAX_S))

        async def gen() -> Any:
            async for item in mon.logs.stream(lvl, since=since, max_s=hold, hello=_hello(device_id, mon)):
                yield json.dumps(item, default=str) + "\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson", headers=_STREAM_HEADERS)

    @router.post("/hub/devices/{device_id}/logs/dump_config")
    async def dump_config(device_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        _device, mon = monitor_for(hub, device_id)
        if mon is None:
            raise HTTPException(409, detail="This device is not an ESPHome node")
        try:
            mon.request_dump_config()
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return {"ok": True, "device_id": device_id}

    _register_ws()
    return router


def _ws_monitor(hub: Any, device_id: Any) -> Any:
    try:
        _device, mon = monitor_for(hub, str(device_id or ""))
    except HTTPException:
        return None
    return mon


def _register_ws() -> None:
    from services.homehub import api as hub_api

    async def subscribe(conn: Any, mid: int, msg: dict[str, Any]) -> None:
        if conn.principal is None or not conn.principal.is_admin:
            await conn.error(mid, "unauthorized", "Only an admin can read a device's logs")
            return
        level = esphome_logs.view_level(msg.get("level"))
        if level is None:
            await conn.error(mid, "invalid_format", "level must be one of " + ", ".join(esphome_logs.VIEW_LEVELS))
            return
        mon = _ws_monitor(conn.hub, msg.get("device_id"))
        if mon is None:
            await conn.error(mid, "not_found", "No ESPHome device with that id")
            return
        since = msg.get("since")
        since = int(since) if isinstance(since, int | float) else None
        await conn.result(mid)

        async def pump() -> None:
            async for item in mon.logs.stream(level, since=since, max_s=None, heartbeat_s=None,
                                              hello=_hello(str(msg.get("device_id")), mon)):
                await conn.send({"id": mid, "type": "event", "event": item})

        task = asyncio.get_running_loop().create_task(pump(), name=f"esphome-logs:{mid}")
        conn.subs[mid] = task.cancel
        # Kept by the connection: when the socket closes every sub is called, which cancels the pump.
        with contextlib.suppress(AttributeError):
            conn._tasks.add(task)
            task.add_done_callback(conn._tasks.discard)

    async def dump(conn: Any, mid: int, msg: dict[str, Any]) -> None:
        if conn.principal is None or not conn.principal.is_admin:
            await conn.error(mid, "unauthorized", "Only an admin can ask a device for its config")
            return
        mon = _ws_monitor(conn.hub, msg.get("device_id"))
        if mon is None:
            await conn.error(mid, "not_found", "No ESPHome device with that id")
            return
        try:
            mon.request_dump_config()
        except RuntimeError as exc:
            await conn.error(mid, "not_connected", str(exc))
            return
        await conn.result(mid)

    hub_api.WS_COMMANDS["homehub/esphome/logs/subscribe"] = subscribe
    hub_api.WS_COMMANDS["homehub/esphome/logs/dump_config"] = dump
