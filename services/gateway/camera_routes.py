"""Cameras: live views and recorded footage from the household's cameras (the Cameras page).

Everything comes from homehub's camera API (``services/homehub/README.md``,
"Cameras"), reached at the caller's own Home Assistant URL with their own HA
token, which identity redirects to the hub. Neither ever reaches the browser:
the page talks only to these routes.

Who sees what follows the device permissions every other HA surface uses
(``services.execution.entity_access``): a camera the caller may not control is
not listed, and its picture, stream and recordings answer 404 as if it did not
exist.

The list and recordings index take the API key. The pictures, the live stream
and the recordings themselves are loaded by ``<img>`` and ``<video>``, which
cannot send a header, so they live under ``/api/media/`` and take the signed
``?mt=&user=`` media token (an API key in the URL is refused there).
"""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from services.execution.entity_access import can_view, load_entity_permissions

log = logging.getLogger("gateway.cameras")

router = APIRouter()

#: Entity ids go into the hub's URL path, so only the plain HA shape passes.
_CAMERA_ID = re.compile(r"^camera\.[a-z0-9_]+$")
_JSON_TIMEOUT = aiohttp.ClientTimeout(total=45, connect=5)
#: A sleeping battery camera takes about 16 s to wake for its first picture (measured), then well under 1 s.
_SNAPSHOT_TIMEOUT = aiohttp.ClientTimeout(total=45, connect=5)
#: A live view or a recording runs as long as someone watches: bound the connect and each read only.
_STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=10, sock_read=45)
#: The headers a player needs from a recording; nothing else of the hub's is passed on.
_RECORDING_HEADERS = ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges")
_NOT_FOUND = "Camera not found"


async def _caller(request: Request, *, media: bool = False) -> dict[str, Any]:
    """The signed-in caller's identity (with their HA URL and token), or 401."""
    from services.gateway import main as gateway

    creds = await gateway._resolve_identity_from_media_token(request) if media else None
    if creds is None:
        creds = await gateway._resolve_identity_from_request(request)
    creds = gateway._identity_cred_dict(creds)
    if not creds.get("user"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return creds


def _hub(creds: dict[str, Any]) -> tuple[str, dict[str, str]]:
    base = str(creds.get("ha_url") or "").rstrip("/")
    token = str(creds.get("ha_token") or "")
    if not base or not token:
        raise HTTPException(status_code=503, detail="Home Assistant is not set up for your account")
    return base, {"Authorization": f"Bearer {token}"}


async def _permissions(creds: dict[str, Any]):
    return await load_entity_permissions(str(creds.get("user") or ""), bool(creds.get("is_admin")))


async def _camera_for(request: Request, entity_id: str, *, media: bool) -> dict[str, Any]:
    """The caller's identity once they may see this camera; 404 when it is not theirs to see."""
    if not _CAMERA_ID.match(entity_id or ""):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    creds = await _caller(request, media=media)
    if not can_view(await _permissions(creds), entity_id):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return creds


async def _hub_message(resp: aiohttp.ClientResponse) -> str:
    try:
        body = await resp.json(content_type=None)
    except Exception:
        return ""
    return str(body.get("message") or "") if isinstance(body, dict) else ""


async def _hub_json(creds: dict[str, Any], path: str, params: dict[str, str] | None = None) -> Any:
    from services.gateway.main import shared_http_client

    base, headers = _hub(creds)
    try:
        async with shared_http_client() as client, client.get(
            f"{base}{path}", params=params or None, headers=headers, timeout=_JSON_TIMEOUT
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            message = await _hub_message(resp)
            status = resp.status
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning("[cameras] hub %s: %s", path, type(exc).__name__)
        raise HTTPException(status_code=502, detail="The camera hub did not answer") from exc
    if status in (400, 404):
        # The hub's own words ("This camera has no recordings", "longer than 7 days") are for people.
        raise HTTPException(status_code=status, detail=message or _NOT_FOUND)
    if status == 503:
        raise HTTPException(status_code=503, detail=message or "The camera is not available")
    log.warning("[cameras] hub %s answered HTTP %s", path, status)
    raise HTTPException(status_code=502, detail="The camera hub did not answer")


async def _hub_open(creds: dict[str, Any], path: str, *, params: dict[str, str] | None = None,
                    headers: dict[str, str] | None = None) -> tuple[aiohttp.ClientResponse, Any]:
    """Open a long response from the hub. The caller streams it and closes the session."""
    from services.gateway.main import _PooledMediaSession

    base, auth = _hub(creds)
    session = _PooledMediaSession(_STREAM_TIMEOUT)
    try:
        resp = await session.get(f"{base}{path}", params=params or None, headers={**auth, **(headers or {})})
    except (TimeoutError, aiohttp.ClientError) as exc:
        await session.close()
        log.warning("[cameras] hub %s: %s", path, type(exc).__name__)
        raise HTTPException(status_code=502, detail="The camera hub did not answer") from exc
    return resp, session


async def _relay(resp: aiohttp.ClientResponse, session: Any, request: Request) -> AsyncIterator[bytes]:
    try:
        async for chunk in resp.content.iter_any():
            if await request.is_disconnected():
                break  # stop asking the hub, which stops waking the camera
            yield chunk
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.info("[cameras] stream ended: %s", type(exc).__name__)
    finally:
        await session.close()


@router.get("/api/cameras")
async def list_cameras(request: Request):
    """The cameras the caller may see, with what each can do (live modes, recordings, battery)."""
    creds = await _caller(request)
    cameras = await _hub_json(creds, "/api/homehub/cameras")
    if not isinstance(cameras, list):
        raise HTTPException(status_code=502, detail="The camera hub did not answer")
    perms = await _permissions(creds)
    visible = [c for c in cameras if isinstance(c, dict) and can_view(perms, str(c.get("entity_id") or ""))]
    return {"cameras": visible}


@router.get("/api/cameras/{entity_id}/recordings")
async def list_recordings(entity_id: str, request: Request, start: str | None = None, end: str | None = None):
    """A camera's recordings between ``start`` and ``end`` (ISO times; default the last 24 h, at most 7 days)."""
    creds = await _camera_for(request, entity_id, media=False)
    params = {k: v for k, v in (("start", start), ("end", end)) if v}
    return await _hub_json(creds, f"/api/homehub/cameras/{entity_id}/recordings", params)


@router.get("/api/media/cameras/{entity_id}/snapshot")
async def camera_snapshot(entity_id: str, request: Request):
    """One picture from the camera, for a tile."""
    from services.gateway.main import shared_http_client

    creds = await _camera_for(request, entity_id, media=True)
    base, headers = _hub(creds)
    status = 0
    try:
        async with shared_http_client() as client, client.get(
            f"{base}/api/camera_proxy/{entity_id}", headers=headers, timeout=_SNAPSHOT_TIMEOUT
        ) as resp:
            if resp.status == 200:
                body = await resp.read()
                ctype = resp.headers.get("Content-Type", "image/jpeg")
            else:
                body, ctype = b"", ""
                status = resp.status
    except (TimeoutError, aiohttp.ClientError) as exc:
        raise HTTPException(status_code=503, detail="The camera is not available") from exc
    if not body:
        if status in (401, 404):
            log.warning("[cameras] snapshot of %s: hub answered HTTP %s", entity_id, status)
        raise HTTPException(status_code=503, detail="The camera is not available")
    return Response(content=body, media_type=ctype if ctype.startswith("image/") else "image/jpeg",
                    headers={"Cache-Control": "private, no-store"})


@router.get("/api/media/cameras/{entity_id}/stream")
async def camera_stream(entity_id: str, request: Request):
    """The live view as ``multipart/x-mixed-replace`` (an ``<img>`` plays it), streamed as it arrives."""
    creds = await _camera_for(request, entity_id, media=True)
    resp, session = await _hub_open(creds, f"/api/camera_proxy_stream/{entity_id}")
    if resp.status != 200:
        message = await _hub_message(resp)
        await session.close()
        raise HTTPException(status_code=503, detail=message or "The camera is not available")
    return StreamingResponse(
        _relay(resp, session, request),
        media_type=resp.headers.get("Content-Type", "multipart/x-mixed-replace; boundary=frame"),
        # nginx in front of the gateway buffers responses; a live view must not wait for a buffer to fill.
        headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/api/media/cameras/{entity_id}/recordings/play")
async def play_recording(entity_id: str, request: Request, file: str = ""):
    """One recording as MP4 for a ``<video>``, streamed, with ``Range`` passed on so seeking works."""
    if not file:
        raise HTTPException(status_code=400, detail="file is required")
    creds = await _camera_for(request, entity_id, media=True)
    range_header = request.headers.get("range")
    resp, session = await _hub_open(creds, f"/api/homehub/cameras/{entity_id}/recordings/play",
                                    params={"file": file}, headers={"Range": range_header} if range_header else None)
    if resp.status not in (200, 206):
        message = await _hub_message(resp)
        status = resp.status
        await session.close()
        if status in (400, 404, 416):
            return JSONResponse({"detail": message or "Recording not found"}, status_code=status)
        raise HTTPException(status_code=503, detail=message or "The recording is not available")
    headers = {k: resp.headers[k] for k in _RECORDING_HEADERS if resp.headers.get(k)}
    headers["Cache-Control"] = "private, no-store"
    headers["X-Accel-Buffering"] = "no"
    return StreamingResponse(_relay(resp, session, request), status_code=resp.status,
                             media_type=headers.pop("Content-Type", "video/mp4"), headers=headers)
