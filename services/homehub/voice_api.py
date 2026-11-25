"""The ``/hub/voice`` admin API: the voice satellites and how their last runs went.

| Route | What |
|---|---|
| ``GET /hub/voice`` | ``settings`` (the hub's ``HOMEHUB_VOICE_*`` values and any ``problems`` naming the one to fix) and ``satellites``: each ESPHome voice satellite with its entity, area, features, audio path, ``enabled``, ``subscribed``, run count, ``last_run`` (outcome, what ended it, error, microphone ``levels``, per-stage ``timings_ms``), ``history`` (the last 20 runs, kept across restarts) and ``last_error`` |
| ``PATCH /hub/voice/{entry_id}`` | ``{"enabled": bool}``: turn one satellite's pipeline on or off (entry option ``voice_pipeline``), applied on its live connection |
| ``GET /hub/voice/hold-music`` | The hold music setting (``hold_music.py``): ``source`` (``gameshow``, ``builtin`` or ``upload``), the ``sources`` to choose from, the hold ``lines`` (empty: the defaults), the ``upload`` kept (name, type, seconds) and the upload ``limits`` |
| ``PUT /hub/voice/hold-music`` | ``{"source"?: ..., "lines"?: [...]}``: change it; applies to the next wait, no restart |
| ``POST /hub/voice/hold-music/clip?filename=`` | The audio file itself as the body (wav, mp3, ogg or m4a; at most 5 MB and 60 s): decoded once, kept in the hub's data (never served back) and made the hold music. 400 (cannot be decoded, too long), 413 (too big), 415 (not one of those types) |
| ``DELETE /hub/voice/hold-music/clip`` | Remove the uploaded clip (the game-show clip plays instead if it was playing) |

No transcript or reply is ever returned. Admins only, like the rest of ``/hub/*``. See ``voice_pipeline.py``.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.homehub import hold_music, voice_pipeline
from services.homehub.auth import Auth, bearer
from services.homehub.core import HubError


def build_router(runtime: Any, auth: Auth) -> APIRouter:
    router = APIRouter()
    hub = runtime.hub

    def admin(request: Request) -> None:
        tok = bearer(request.headers.get("authorization")) or request.headers.get("x-internal-secret")
        p = auth.authenticate(tok)
        if p is None or not p.is_admin:
            raise HTTPException(401, detail="Unauthorized")

    @router.get("/hub/voice")
    async def voice_status(request: Request) -> dict[str, Any]:
        admin(request)
        try:
            settings: dict[str, Any] = voice_pipeline.current_settings().public()
        except HubError as exc:
            settings = {"problems": [str(exc)]}
        return {"settings": settings, "satellites": voice_pipeline.satellites(hub)}

    @router.patch("/hub/voice/{entry_id}")
    async def set_enabled(entry_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            raise HTTPException(400, detail="The body must be {\"enabled\": true or false}")
        entry = hub.config_entries.get(entry_id)
        if entry is None:
            raise HTTPException(404, detail="Config entry not found")
        voice = getattr(hub.drivers.get(entry_id), "voice", None)
        if entry.domain != "esphome" or not isinstance(voice, voice_pipeline.SatelliteVoice):
            raise HTTPException(409, detail=f"{entry.title} is not a running ESPHome node")
        entry.options["voice_pipeline"] = body["enabled"]
        runtime.store.save_config_entry(entry)
        driver = hub.drivers[entry_id]
        client = getattr(driver, "_client", None)
        if client is not None and getattr(driver, "_device_info", None) is not None:
            voice.attach(client, driver._device_info)
        return voice.status()

    @router.get("/hub/voice/hold-music")
    async def get_hold_music(request: Request) -> dict[str, Any]:
        admin(request)
        return hold_music.settings(hub)

    @router.put("/hub/voice/hold-music")
    async def put_hold_music(request: Request) -> dict[str, Any]:
        admin(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise HTTPException(400, detail="The body must be a JSON object")
        try:
            return hold_music.save_settings(hub, body)
        except hold_music.ClipError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from exc

    @router.post("/hub/voice/hold-music/clip")
    async def upload_hold_music(request: Request, filename: str = "") -> dict[str, Any]:
        admin(request)
        declared = request.headers.get("content-length") or ""
        if declared.isdigit() and int(declared) > hold_music.MAX_UPLOAD_BYTES:
            raise HTTPException(413, detail=f"The most is {hold_music.MAX_UPLOAD_BYTES // 1048576} MB")
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > hold_music.MAX_UPLOAD_BYTES:
                raise HTTPException(413, detail=f"The most is {hold_music.MAX_UPLOAD_BYTES // 1048576} MB")
        try:  # ffmpeg and the resampling are slow next to the event loop
            return await asyncio.to_thread(hold_music.store_upload, hub, bytes(data), filename)
        except hold_music.ClipError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from exc

    @router.delete("/hub/voice/hold-music/clip")
    async def delete_hold_music(request: Request) -> dict[str, Any]:
        admin(request)
        try:
            return hold_music.delete_upload(hub)
        except hold_music.ClipError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from exc

    return router
