"""The ``/hub/voice`` admin API: the voice satellites and how their last runs went.

| Route | What |
|---|---|
| ``GET /hub/voice`` | ``settings`` (the hub's ``HOMEHUB_VOICE_*`` values and any ``problems`` naming the one to fix) and ``satellites``: each ESPHome voice satellite with its entity, area, features, audio path, ``enabled``, ``subscribed``, run count, ``last_run`` (outcome, what ended it, error, microphone ``levels``, per-stage ``timings_ms``), ``history`` (the last 20 runs, kept across restarts) and ``last_error`` |
| ``PATCH /hub/voice/{entry_id}`` | ``{"enabled": bool}``: turn one satellite's pipeline on or off (entry option ``voice_pipeline``), applied on its live connection |
| ``GET /hub/voice/hold-music`` | The hold music setting (``hold_music.py``): ``source`` (``gameshow``, ``builtin`` or ``upload``), the ``sources`` to choose from, the hold ``lines`` (empty: the defaults), the ``upload`` kept (name, type, seconds) and the upload ``limits`` |
| ``PUT /hub/voice/hold-music`` | ``{"source"?: ..., "lines"?: [...]}``: change it; applies to the next wait, no restart |
| ``POST /hub/voice/hold-music/clip?filename=`` | The audio file itself as the body (wav, mp3, ogg or m4a; at most 5 MB and 60 s): decoded once, kept in the hub's data (never served back) and made the hold music. 400 (cannot be decoded, too long), 413 (too big), 415 (not one of those types) |
| ``DELETE /hub/voice/hold-music/clip`` | Remove the uploaded clip (the game-show clip plays instead if it was playing) |
| ``GET /hub/voice/settings`` | ``whisper``: whisper mode as set (``enabled``, the reply ``volume`` 0 to 1, ``level_drop_db``) and its ``defaults`` |
| ``PUT /hub/voice/settings`` | ``{"whisper": {...}}`` with any of those fields; kept in the hub's store and used from the next command, no restart. An invalid value is a 400 naming it |
| ``GET /hub/voice/devices`` | ``devices``: every voice satellite and speaker/TV media player, one per physical device, keyed by a stable ``hardware_id`` (its MAC, else its first identifier, else the hub's device id), with its ``kind``, name, area and ``entity_ids``. Identity copies these so an admin can assign each to a person (services/identity/hub_devices.py) |

No transcript or reply is ever returned. Admins only, like the rest of ``/hub/*``. See ``voice_pipeline.py``.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.homehub import hold_music, voice_pipeline, voice_whisper
from services.homehub.auth import Auth, bearer
from services.homehub.core import HubError

#: Media players that stand for other players (a group, Music Assistant's mirror of
#: a real speaker), so the physical speaker is not listed twice.
VIRTUAL_PLAYER_PLATFORMS = frozenset({"group", "universal", "music_assistant"})


def _hardware_id(dev: Any, entity: Any) -> str:
    """The identity that survives renames: a MAC, else an integration identifier, else the hub's id."""
    for conn in getattr(dev, "connections", None) or []:
        if len(conn) == 2 and str(conn[0]).lower() == "mac" and conn[1]:
            return "mac:" + str(conn[1]).lower()
    for ident in getattr(dev, "identifiers", None) or []:
        if len(ident) == 2 and ident[1]:
            return re.sub(r"\s+", "_", f"{ident[0]}:{ident[1]}".lower())[:80]
    if dev is not None:
        return f"hub:{dev.id}"
    return f"entity:{entity.platform}:{entity.unique_id or entity.entity_id}".lower()[:80]


def shared_devices(hub: Any) -> list[dict[str, Any]]:
    """Every voice satellite and speaker/TV the hub knows, one row per physical device.

    A device with an ``assist_satellite`` entity is a ``voice_satellite`` (its
    speaker, if any, is listed with it); any other device with a media player is
    a ``tv`` (device class tv) or a ``speaker``. Hidden, disabled and archived
    entities are left out, and so are virtual players
    (``VIRTUAL_PLAYER_PLATFORMS``).
    """
    groups: dict[str, dict[str, Any]] = {}
    for entity in list(hub.entities.values()):
        domain = entity.entity_id.split(".", 1)[0]
        if domain not in ("assist_satellite", "media_player"):
            continue
        if entity.disabled_by or hub.is_hidden(entity.entity_id):
            continue
        if domain == "media_player" and entity.platform in VIRTUAL_PLAYER_PLATFORMS:
            continue
        dev = hub.devices.get(entity.device_id or "")
        hardware_id = _hardware_id(dev, entity)
        group = groups.setdefault(hardware_id, {
            "hardware_id": hardware_id, "hub_device_id": dev.id if dev else None, "satellite": False, "tv": False,
            "name": (dev.display_name if dev else "") or "", "entity_ids": [],
            "manufacturer": getattr(dev, "manufacturer", None), "model": getattr(dev, "model", None),
            "area_id": (dev.area_id if dev else None),
        })
        group["entity_ids"].append(entity.entity_id)
        group["area_id"] = group["area_id"] or hub.entity_area_id(entity.entity_id)
        if domain == "assist_satellite":
            group["satellite"] = True
        else:
            st = hub.states.get(entity.entity_id)
            device_class = (entity.device_class or entity.original_device_class
                            or ((st.attributes or {}).get("device_class") if st else None))
            group["tv"] = group["tv"] or device_class == "tv"
        if not group["name"]:
            st = hub.states.get(entity.entity_id)
            group["name"] = ((st.attributes or {}).get("friendly_name") if st else None) or entity.name or entity.entity_id
    out = []
    for g in groups.values():
        area = hub.areas.get(g["area_id"] or "")
        out.append({
            "hardware_id": g["hardware_id"], "hub_device_id": g["hub_device_id"],
            "kind": "voice_satellite" if g["satellite"] else "tv" if g["tv"] else "speaker",
            "name": g["name"], "area": area.name if area else None, "entity_ids": sorted(g["entity_ids"]),
            "manufacturer": g["manufacturer"], "model": g["model"],
        })
    return sorted(out, key=lambda d: (d["kind"], d["name"].lower()))


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

    def whisper_defaults() -> voice_whisper.WhisperSettings:
        try:
            return voice_whisper.WhisperSettings(volume=voice_pipeline.current_settings().whisper_volume)
        except HubError:
            return voice_whisper.WhisperSettings()

    def settings_payload() -> dict[str, Any]:
        defaults = whisper_defaults()
        current = voice_whisper.load_settings(runtime.store, defaults.volume)
        return {"whisper": current.public(), "defaults": {"whisper": defaults.public()},
                "level_drop_range_db": list(voice_whisper.LEVEL_DROP_RANGE)}

    @router.get("/hub/voice/settings")
    async def voice_settings(request: Request) -> dict[str, Any]:
        admin(request)
        return settings_payload()

    @router.put("/hub/voice/settings")
    async def put_voice_settings(request: Request) -> dict[str, Any]:
        admin(request)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("whisper"), dict) or set(body) - {"whisper"}:
            raise HTTPException(400, detail='The body must be {"whisper": {"enabled"?, "volume"?, "level_drop_db"?}}')
        current = voice_whisper.load_settings(runtime.store, whisper_defaults().volume)
        try:
            updated = voice_whisper.validate_settings(body["whisper"], current)
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from None
        runtime.store.set_kv(voice_whisper.SETTINGS_KV, updated.public())
        return settings_payload()

    @router.get("/hub/voice/devices")
    async def voice_devices(request: Request) -> dict[str, Any]:
        admin(request)
        return {"devices": shared_devices(hub)}

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
