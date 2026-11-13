"""Cameras: real-time live video (WebRTC, MSE, MP4) through homehub's go2rtc integration.

The browser never talks to go2rtc's API (it has no auth and can be told to fetch
any URL). Signaling and the MSE/MP4 streams come through here and the hub
(``services/homehub/live_video_api.py``), with the same rules as the other
camera routes (``camera_routes.py``): the caller's own HA URL and token, and a
camera the caller may not see answers 404. Only the WebRTC media itself flows
directly between the browser and go2rtc's WebRTC port.

| Route | What |
|---|---|
| ``POST /api/media/cameras/{id}/live/webrtc[?quality=]`` | WebRTC offer in (JSON or SDP), answer out |
| ``WS /api/media/cameras/{id}/live/ws?mt=&user=[&quality=]`` | go2rtc's WebSocket (MSE), relayed |
| ``GET /api/media/cameras/{id}/live/stream.mp4?mt=&user=[&quality=]`` | The live view as one MP4, for a plain ``<video>`` |
| ``GET/PUT /api/admin/cameras/live-video``, ``POST .../sync`` | Admins: the go2rtc integration's settings |
| ``GET/PUT /api/admin/cameras/recording`` | Admins: motion recording for cameras that cannot record themselves |

Under ``/api/media/`` the signed media token is accepted (a WebSocket and a
``<video>`` cannot send a header); the WebSocket takes only that.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

from services.gateway import camera_routes
from services.gateway.config import INTERNAL_SECRET

log = logging.getLogger("gateway.cameras.live")

router = APIRouter()

HOMEHUB_URL = os.getenv("HOMEHUB_URL", "http://host.docker.internal:8123").rstrip("/")
QUALITIES = ("sub", "main")
MAX_OFFER_BYTES = 64 * 1024
#: The hub answers once the camera streams; a sleeping battery camera takes about 16 s to wake.
_SIGNAL_TIMEOUT = aiohttp.ClientTimeout(total=50, connect=5)
_ADMIN_TIMEOUT = aiohttp.ClientTimeout(total=40, connect=5)
#: What a browser may send on the live WebSocket (the hub filters again).
WS_CLIENT_TYPES = frozenset({"mse", "mp4", "webrtc", "webrtc/offer", "webrtc/candidate"})
_WS_MAX_MESSAGE = 16 * 1024 * 1024
_PASS_THROUGH = {400, 404, 409, 415, 503}


def _quality(quality: str | None) -> dict[str, str]:
    return {"quality": quality} if quality in QUALITIES else {}


@router.post("/api/media/cameras/{entity_id}/live/webrtc")
async def live_webrtc(entity_id: str, request: Request, quality: str | None = None):
    """Exchange a WebRTC offer for go2rtc's answer; the media then flows straight to go2rtc."""
    from services.gateway.main import shared_http_client

    creds = await camera_routes._camera_for(request, entity_id, media=True)
    base, headers = camera_routes._hub(creds)
    ctype = (request.headers.get("content-type") or "application/json").split(";")[0].strip().lower()
    if ctype not in ("application/json", "application/sdp"):
        raise HTTPException(status_code=415, detail="Send the offer as application/json or application/sdp")
    body = await request.body()
    if not body or len(body) > MAX_OFFER_BYTES:
        raise HTTPException(status_code=400, detail="A WebRTC offer is required")
    try:
        async with shared_http_client() as client, client.post(
            f"{base}/api/homehub/cameras/{entity_id}/live/webrtc", params=_quality(quality) or None, data=body,
            headers={**headers, "Content-Type": ctype}, timeout=_SIGNAL_TIMEOUT,
        ) as resp:
            status = resp.status
            answer = await resp.read()
            answer_type = resp.headers.get("Content-Type", ctype).split(";")[0]
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning("[cameras] live webrtc %s: %s", entity_id, type(exc).__name__)
        raise HTTPException(status_code=502, detail="The camera hub did not answer") from exc
    if status in (200, 201):
        return Response(answer, status_code=status, media_type=answer_type, headers={"Cache-Control": "no-store"})
    return _hub_failure(status, answer)


def _hub_failure(status: int, body: bytes) -> JSONResponse:
    message = ""
    with contextlib.suppress(ValueError, AttributeError):
        message = str(json.loads(body).get("message") or "")
    if status in _PASS_THROUGH:
        return JSONResponse({"detail": message or "No live video for this camera"}, status_code=status)
    log.warning("[cameras] hub answered live video with HTTP %s", status)
    return JSONResponse({"detail": "The camera's live video could not start"}, status_code=502)


@router.get("/api/media/cameras/{entity_id}/live/stream.mp4")
async def live_mp4(entity_id: str, request: Request, quality: str | None = None):
    """The live view as one fragmented MP4, the simplest player (a ``<video src>``) can show."""
    creds = await camera_routes._camera_for(request, entity_id, media=True)
    resp, session = await camera_routes._hub_open(creds, f"/api/homehub/cameras/{entity_id}/live/stream.mp4",
                                                  params=_quality(quality))
    if resp.status != 200:
        status = resp.status
        body = await resp.read()
        await session.close()
        return _hub_failure(status, body)
    return StreamingResponse(camera_routes._relay(resp, session, request),
                             media_type=resp.headers.get("Content-Type", "video/mp4"),
                             headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"})


def _ws_url(base: str, path: str) -> str:
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):] + path
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):] + path
    return base + path


@router.websocket("/api/media/cameras/{entity_id}/live/ws")
async def live_ws(websocket: WebSocket, entity_id: str):
    """go2rtc's WebSocket (MSE video) for one camera. Closing it is what lets the camera stop streaming."""
    if not websocket.query_params.get("mt"):
        await websocket.close(code=4401, reason="A media token is required")
        return
    try:
        creds = await camera_routes._camera_for(websocket, entity_id, media=True)  # type: ignore[arg-type]
        base, headers = camera_routes._hub(creds)
    except HTTPException as exc:
        await websocket.close(code=4000 + min(exc.status_code, 999), reason=str(exc.detail)[:120])
        return
    await websocket.accept()
    quality = websocket.query_params.get("quality")
    session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())
    try:
        async with session.ws_connect(
            _ws_url(base, f"/api/homehub/cameras/{entity_id}/live/ws"), params=_quality(quality) or None,
            headers=headers, heartbeat=30, max_msg_size=_WS_MAX_MESSAGE,
            timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=5),
        ) as upstream:
            await _relay_ws(websocket, upstream)
    except aiohttp.WSServerHandshakeError as exc:
        log.info("[cameras] live ws %s: hub refused (%s)", entity_id, exc.status)
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.info("[cameras] live ws %s ended: %s", entity_id, type(exc).__name__)
    finally:
        await session.close()
        with contextlib.suppress(Exception):
            await websocket.close()


async def _relay_ws(client: WebSocket, upstream: Any) -> None:
    """Media down to the browser; only playback messages up. Ends when either side closes."""

    async def down() -> None:
        async for msg in upstream:
            if msg.type == aiohttp.WSMsgType.BINARY:
                await client.send_bytes(msg.data)
            elif msg.type == aiohttp.WSMsgType.TEXT:
                await client.send_text(msg.data)
            else:
                break

    async def up() -> None:
        while True:
            msg = await client.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            text = msg.get("text")
            if text is None:
                continue
            try:
                kind = json.loads(text).get("type")
            except (ValueError, AttributeError):
                continue
            if kind in WS_CLIENT_TYPES:
                await upstream.send_str(text)

    tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, Exception):
                await t
        with contextlib.suppress(Exception):
            await upstream.close()


# -- admins: the go2rtc integration's settings --------------------------------------------

async def _admin_hub(method: str, path: str, json_body: Any = None) -> Any:
    from services.gateway.main import shared_http_client

    try:
        async with shared_http_client() as client, client.request(
            method, f"{HOMEHUB_URL}{path}", json=json_body, headers={"Authorization": f"Bearer {INTERNAL_SECRET}"},
            timeout=_ADMIN_TIMEOUT,
        ) as resp:
            status = resp.status
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = None
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning("[cameras] hub %s %s: %s", method, path, type(exc).__name__)
        raise HTTPException(status_code=502, detail="The device hub did not answer") from exc
    if status == 200:
        return body
    message = str(body.get("message") or "") if isinstance(body, dict) else ""
    if status in _PASS_THROUGH and message:
        return JSONResponse({"detail": message}, status_code=status)
    log.warning("[cameras] hub %s %s answered HTTP %s", method, path, status)
    raise HTTPException(status_code=502, detail="The device hub did not answer")


async def _admin(request: Request) -> None:
    from services.gateway import main as gateway

    await gateway._require_admin(request)


@router.get("/api/admin/cameras/live-video")
async def get_live_settings(request: Request):
    """The go2rtc integration: whether it runs, its WebRTC address and port, and each camera's streams."""
    await _admin(request)
    return await _admin_hub("GET", "/hub/live-video")


@router.put("/api/admin/cameras/live-video")
async def put_live_settings(request: Request):
    """Change it (``url``, ``enabled``, ``webrtc_host``, ``webrtc_port``, ``default_quality``, ``cameras``)."""
    await _admin(request)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="The body must be a JSON object")
    return await _admin_hub("PUT", "/hub/live-video", body)


@router.post("/api/admin/cameras/live-video/sync")
async def sync_live(request: Request):
    """Register every camera with go2rtc now (it also happens every minute)."""
    await _admin(request)
    return await _admin_hub("POST", "/hub/live-video/sync")


@router.get("/api/admin/cameras/recording")
async def get_recording_settings(request: Request):
    """Motion recording (``services/homehub/camera_recording.py``): the Nextcloud account, folder and cameras."""
    await _admin(request)
    return await _admin_hub("GET", "/hub/camera-recording")


@router.put("/api/admin/cameras/recording")
async def put_recording_settings(request: Request):
    """Change it (``account``, ``url``, ``username``, ``password``, ``folder``, ``enabled``, ``cameras``)."""
    await _admin(request)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="The body must be a JSON object")
    return await _admin_hub("PUT", "/hub/camera-recording", body)
