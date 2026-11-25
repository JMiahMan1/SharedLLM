"""The ``/hub/voice`` admin API: the voice satellites and how their last runs went.

| Route | What |
|---|---|
| ``GET /hub/voice`` | ``settings`` (the hub's ``HOMEHUB_VOICE_*`` values and any ``problems`` naming the one to fix) and ``satellites``: each ESPHome voice satellite with its entity, area, features, audio path, ``enabled``, ``subscribed``, run count, ``last_run`` (outcome, what ended it, error, microphone ``levels``, per-stage ``timings_ms``), ``history`` (the last 20 runs, kept across restarts) and ``last_error`` |
| ``PATCH /hub/voice/{entry_id}`` | ``{"enabled": bool}``: turn one satellite's pipeline on or off (entry option ``voice_pipeline``), applied on its live connection |
| ``GET /hub/voice/devices`` | ``devices``: every voice satellite and speaker/TV media player, one per physical device, keyed by a stable ``hardware_id`` (its MAC, else its first identifier, else the hub's device id), with its ``kind``, name, area and ``entity_ids``. Identity copies these so an admin can assign each to a person (services/identity/hub_devices.py) |

No transcript or reply is ever returned. Admins only, like the rest of ``/hub/*``. See ``voice_pipeline.py``.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.homehub import voice_pipeline
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

    return router
