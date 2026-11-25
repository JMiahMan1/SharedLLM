"""Gateway routes for the voice satellites' hold music (HomeHub's ``/hub/voice/hold-music``), admins only.

| Route | What |
|---|---|
| ``GET /api/homehub/voice/hold-music`` | The setting: ``source``, the ``sources`` to choose from, ``lines``, ``upload``, ``limits`` |
| ``PUT /api/homehub/voice/hold-music`` | ``{"source"?: "gameshow" or "builtin" or "upload", "lines"?: [...]}`` |
| ``POST /api/homehub/voice/hold-music/clip`` | multipart ``file``: an audio clip to play instead (wav, mp3, ogg, m4a; 5 MB, 60 s) |
| ``DELETE /api/homehub/voice/hold-music/clip`` | Remove the uploaded clip |

The clip goes to the hub as it came (the hub decodes and checks it); nothing here keeps or serves it.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from services.gateway import automation_routes
from services.gateway.config import INTERNAL_SECRET

log = logging.getLogger("gateway.voice_hold_music")

router = APIRouter(prefix="/api/homehub/voice/hold-music")

#: The hub's own limit; checked here too so a big file is refused before it travels further.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
#: Decoding an mp3 or m4a with ffmpeg and resampling takes a few seconds on the hub.
_UPLOAD_TIMEOUT = aiohttp.ClientTimeout(total=120, connect=5)
#: The hub's answers to an upload that are meant for people (cannot decode, too big, wrong type).
_UPLOAD_PASS_THROUGH = {400, 403, 404, 413, 415, 503}


@router.get("")
async def get_hold_music(request: Request):
    await automation_routes._admin(request)
    return automation_routes._answer(*await automation_routes._hub("GET", "/hub/voice/hold-music"))


@router.put("")
async def put_hold_music(request: Request):
    await automation_routes._admin(request)
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="The body must be a JSON object")
    payload = {k: body[k] for k in ("source", "lines") if k in body}
    return automation_routes._answer(*await automation_routes._hub("PUT", "/hub/voice/hold-music", json=payload))


async def _read_clip(request: Request) -> tuple[bytes, str]:
    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=422, detail="Attach the audio file as 'file'")
    raw = await upload.read()
    if not raw:
        raise HTTPException(status_code=400, detail="The file is empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"The most is {MAX_UPLOAD_BYTES // 1048576} MB")
    return raw, str(getattr(upload, "filename", "") or "")


async def _send_clip(raw: bytes, filename: str) -> tuple[int, Any]:
    from services.gateway.main import shared_http_client

    url = f"{automation_routes._homehub_base()}/hub/voice/hold-music/clip"
    headers = {"Authorization": f"Bearer {INTERNAL_SECRET}", "Content-Type": "application/octet-stream"}
    try:
        async with shared_http_client() as client, client.post(
            url, params={"filename": filename[:200]}, data=raw, headers=headers, timeout=_UPLOAD_TIMEOUT
        ) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = None
            return resp.status, body
    except (TimeoutError, aiohttp.ClientError) as exc:
        log.warning("[hold music] hub upload: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="The device hub did not answer") from exc


@router.post("/clip")
async def upload_clip(request: Request):
    await automation_routes._admin(request)
    raw, filename = await _read_clip(request)
    status, body = await _send_clip(raw, filename)
    if status == 200:
        return body
    if status in _UPLOAD_PASS_THROUGH and isinstance(body, dict):
        message = str(body.get("message") or body.get("detail") or "")
        return JSONResponse({"detail": message or "The clip was refused"}, status_code=status)
    log.warning("[hold music] hub answered the upload with HTTP %s", status)
    raise HTTPException(status_code=502, detail="The device hub did not answer")


@router.delete("/clip")
async def delete_clip(request: Request):
    await automation_routes._admin(request)
    return automation_routes._answer(*await automation_routes._hub("DELETE", "/hub/voice/hold-music/clip"))
