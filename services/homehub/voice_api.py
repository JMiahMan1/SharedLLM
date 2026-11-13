"""The ``/hub/voice`` admin API: the voice satellites and how their last runs went.

| Route | What |
|---|---|
| ``GET /hub/voice`` | ``settings`` (the hub's ``HOMEHUB_VOICE_*`` values and any ``problems`` naming the one to fix) and ``satellites``: each ESPHome voice satellite with its entity, area, features, audio path, ``enabled``, ``subscribed``, run count, ``last_run`` (outcome, what ended it, error, microphone ``levels``, per-stage ``timings_ms``), ``history`` (the last 20 runs, kept across restarts) and ``last_error`` |
| ``PATCH /hub/voice/{entry_id}`` | ``{"enabled": bool}``: turn one satellite's pipeline on or off (entry option ``voice_pipeline``), applied on its live connection |

No transcript or reply is ever returned. Admins only, like the rest of ``/hub/*``. See ``voice_pipeline.py``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.homehub import voice_pipeline
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

    return router
