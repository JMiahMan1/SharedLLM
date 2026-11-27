"""Ambient light ring intelligence for Echo satellites.

Home Hub helper functions and a ``register_services(hub)`` entry point. Each
helper resolves the Echo's ``light.*_light_ring`` entity (and, where needed,
``number.*_light_ring_fill``) and drives it through the hub's normal service
calls, so automations, scripts and the API all share one implementation.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from services.homehub.core import HubError

log = logging.getLogger("homehub.echo_ring")

_BACKGROUND_TASKS: set[asyncio.Task] = set()

DOMAIN = "echo_ring"

PRESETS: dict[str, str] = {
    "notice": "#0000ff",
    "alert": "#ff0000",
    "doorbell": "#ffff00",
    "success": "#00ff00",
    "drop_in": "#8000ff",
}

MUSIC_EFFECTS = (
    "Music beat",
    "Music spectrum",
    "Music VU meter",
    "Music orbit",
    "Music drums",
    "Music aurora",
)


def _hub_from(hub: Any, kw: dict[str, Any]) -> Any:
    if hub is None and "hub" in kw:
        hub = kw.pop("hub")
    return hub


def _hex_to_rgb(color: str) -> list[int]:
    color = str(color).strip()
    if color.lower() in PRESETS:
        color = PRESETS[color.lower()]
    if not color.startswith("#"):
        # named but unknown: treat as a preset miss -> fail fast
        raise HubError(f"echo_ring: unknown color {color!r}")
    hexv = color.lstrip("#")
    if len(hexv) == 3:
        hexv = "".join(c * 2 for c in hexv)
    if len(hexv) != 6:
        raise HubError(f"echo_ring: color must be #rrggbb, not {color!r}")
    try:
        r = int(hexv[0:2], 16)
        g = int(hexv[2:4], 16)
        b = int(hexv[4:6], 16)
    except ValueError as exc:
        raise HubError(f"echo_ring: invalid color {color!r}") from exc
    return [r, g, b]


def _resolve_device_id(hub: Any, device_id: str | None, entity_id: str | None) -> str | None:
    if device_id is not None:
        return device_id
    if entity_id is None:
        return None
    ent = hub.entities.get(entity_id)
    if ent is not None:
        return getattr(ent, "device_id", None)
    return None


def _light_ring_entity(hub: Any, device_id: str | None, entity_id: str | None) -> str:
    if device_id is None and entity_id is None:
        raise HubError("echo_ring: device_id or entity_id is required")
    # explicit entity_id that is itself a light ring
    if entity_id is not None and entity_id in hub.entities:
        ent = hub.entities[entity_id]
        if ent.domain == "light" and "light_ring" in entity_id.lower():
            return entity_id
    dev = _resolve_device_id(hub, device_id, entity_id)
    matches = []
    for eid, ent in hub.entities.items():
        if ent.domain != "light":
            continue
        if dev is not None and getattr(ent, "device_id", None) != dev:
            continue
        low = (eid + " " + str(getattr(ent, "unique_id", "") or "") + " " +
               str(getattr(ent, "original_name", "") or "") + " " +
               str(getattr(ent, "name", "") or "")).lower()
        if "light_ring" not in low and "light ring" not in low:
            continue
        matches.append(eid)
    # prefer exact suffix
    for eid in matches:
        if eid.lower().endswith("_light_ring") or eid.lower().endswith(".light_ring"):
            return eid
    if matches:
        return matches[0]
    raise HubError("echo_ring: this device has no light.*_light_ring entity")


def _fill_entity(hub: Any, device_id: str | None, entity_id: str | None) -> str:
    if device_id is None and entity_id is None:
        raise HubError("echo_ring: device_id or entity_id is required")
    dev = _resolve_device_id(hub, device_id, entity_id)
    # if entity_id is a ring, derive device
    if dev is None and entity_id is not None and entity_id in hub.entities:
        dev = getattr(hub.entities[entity_id], "device_id", None)
    matches = []
    for eid, ent in hub.entities.items():
        if ent.domain != "number":
            continue
        if dev is not None and getattr(ent, "device_id", None) != dev:
            continue
        low = (eid + " " + str(getattr(ent, "unique_id", "") or "") + " " +
               str(getattr(ent, "original_name", "") or "") + " " +
               str(getattr(ent, "name", "") or "")).lower()
        if "light_ring_fill" not in low and "light ring fill" not in low:
            continue
        matches.append(eid)
    for eid in matches:
        if "light_ring_fill" in eid.lower():
            return eid
    if matches:
        return matches[0]
    raise HubError("echo_ring: this device has no number.*_light_ring_fill entity")


def _node_from_ring(ring_eid: str) -> str:
    obj = ring_eid.split(".", 1)[-1]
    suffix = "_light_ring"
    if obj.lower().endswith(suffix):
        return obj[: -len(suffix)]
    return obj


async def notify(hub: Any = None, device_id: str | None = None, entity_id: str | None = None,
                 color: str = "notice", effect: str = "Pulse", brightness: int = 200,
                 duration_s: float = 0, **kw: Any) -> dict[str, Any]:
    hub = _hub_from(hub, kw)
    device_id = kw.pop("device_id", device_id)
    entity_id = kw.pop("entity_id", entity_id)
    color = kw.pop("color", color)
    effect = kw.pop("effect", effect)
    brightness = kw.pop("brightness", brightness)
    duration_s = kw.pop("duration_s", duration_s)
    ring = _light_ring_entity(hub, device_id, entity_id)
    rgb = _hex_to_rgb(color)
    await hub.call_service("light", "turn_on", {
        "entity_id": ring,
        "rgb_color": rgb,
        "effect": effect,
        "brightness": brightness,
    })
    if duration_s and duration_s > 0:
        async def _off() -> None:
            await asyncio.sleep(duration_s)
            try:
                await hub.call_service("light", "turn_off", {"entity_id": ring})
            except Exception as exc:  # pragma: no cover - best effort
                log.warning("echo_ring: turn_off failed: %s", exc)
        task = asyncio.create_task(_off())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
    return {"entity_id": ring}


async def progress(hub: Any = None, device_id: str | None = None, entity_id: str | None = None,
                   percent: Any = 0, color: str | None = None, **kw: Any) -> dict[str, Any]:
    hub = _hub_from(hub, kw)
    device_id = kw.pop("device_id", device_id)
    entity_id = kw.pop("entity_id", entity_id)
    percent = kw.pop("percent", percent)
    color = kw.pop("color", color)
    try:
        pct = float(percent)
    except (TypeError, ValueError) as exc:
        raise HubError(f"echo_ring: percent must be a number, not {percent!r}") from exc
    if pct < 0 or pct > 100:
        raise HubError(f"echo_ring: percent must be 0..100, not {percent!r}")
    ring = _light_ring_entity(hub, device_id, entity_id)
    data: dict[str, Any] = {"entity_id": ring}
    if color is not None:
        data["rgb_color"] = _hex_to_rgb(color)
    await hub.call_service("light", "turn_on", data)
    fill = _fill_entity(hub, device_id, entity_id)
    await hub.call_service("number", "set_value", {"entity_id": fill, "value": pct})
    return {"entity_id": ring, "fill_entity_id": fill}


async def music_mode(hub: Any = None, device_id: str | None = None, entity_id: str | None = None,
                     effect: str = "", brightness: int = 200, **kw: Any) -> dict[str, Any]:
    hub = _hub_from(hub, kw)
    device_id = kw.pop("device_id", device_id)
    entity_id = kw.pop("entity_id", entity_id)
    effect = kw.pop("effect", effect)
    brightness = kw.pop("brightness", brightness)
    if effect not in MUSIC_EFFECTS:
        raise HubError(f"echo_ring: unknown music effect {effect!r}")
    ring = _light_ring_entity(hub, device_id, entity_id)
    await hub.call_service("light", "turn_on", {
        "entity_id": ring,
        "effect": effect,
        "brightness": brightness,
    })
    return {"entity_id": ring}


async def flash_all(hub: Any = None, color: str = "#ff0000", repeat: int = 3,
                    brightness: int = 255, frames: Any = None, **kw: Any) -> dict[str, Any]:
    hub = _hub_from(hub, kw)
    color = kw.pop("color", color)
    repeat = kw.pop("repeat", repeat)
    brightness = kw.pop("brightness", brightness)
    frames = kw.pop("frames", frames)
    rgb = _hex_to_rgb(color)
    if frames is None:
        hex6 = f"{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
        anim_frames = [f"200:{','.join([hex6] * 12)}", f"200:{','.join(['000000'] * 12)}"]
    else:
        anim_frames = frames
    targeted: list[str] = []
    failed: list[str] = []
    rings = []
    seen = set()
    for eid, ent in hub.entities.items():
        if ent.domain != "light":
            continue
        low = (eid + " " + str(getattr(ent, "unique_id", "") or "") + " " +
               str(getattr(ent, "original_name", "") or "")).lower()
        if "light_ring" not in low and "light ring" not in low:
            continue
        if eid in seen:
            continue
        seen.add(eid)
        rings.append(eid)
    for ring in rings:
        node = _node_from_ring(ring)
        anim_svc = f"{node}_light_ring_animation"
        try:
            if ("esphome", anim_svc) in getattr(hub, "services", {}):
                await hub.call_service("esphome", anim_svc, {
                    "repeat": repeat,
                    "brightness": brightness,
                    "frames": anim_frames,
                })
            else:
                await hub.call_service("light", "turn_on", {
                    "entity_id": ring,
                    "rgb_color": rgb,
                    "effect": "Blink",
                    "brightness": brightness,
                })
            targeted.append(ring)
        except Exception as exc:
            log.warning("echo_ring: flash failed for %s: %s", ring, exc)
            failed.append(ring)
    return {"targeted": targeted, "failed": failed}


async def clear(hub: Any = None, device_id: str | None = None, entity_id: str | None = None, **kw: Any) -> dict[str, Any]:
    hub = _hub_from(hub, kw)
    device_id = kw.pop("device_id", device_id)
    entity_id = kw.pop("entity_id", entity_id)
    ring = _light_ring_entity(hub, device_id, entity_id)
    node = _node_from_ring(ring)
    stop_svc = f"{node}_light_ring_stop"
    if ("esphome", stop_svc) in getattr(hub, "services", {}):
        await hub.call_service("esphome", stop_svc, {})
    else:
        await hub.call_service("light", "turn_off", {"entity_id": ring})
    return {"entity_id": ring}


def register_services(hub: Any) -> None:
    async def _notify(_hub: Any, call: Any) -> dict[str, Any]:
        data = dict(call.data or {})
        return await notify(hub=_hub, **data)

    async def _progress(_hub: Any, call: Any) -> dict[str, Any]:
        data = dict(call.data or {})
        return await progress(hub=_hub, **data)

    async def _music_mode(_hub: Any, call: Any) -> dict[str, Any]:
        data = dict(call.data or {})
        return await music_mode(hub=_hub, **data)

    async def _flash_all(_hub: Any, call: Any) -> dict[str, Any]:
        data = dict(call.data or {})
        return await flash_all(hub=_hub, **data)

    async def _clear(_hub: Any, call: Any) -> dict[str, Any]:
        data = dict(call.data or {})
        return await clear(hub=_hub, **data)

    hub.register_service(DOMAIN, "notify", _notify, {})
    hub.register_service(DOMAIN, "progress", _progress, {})
    hub.register_service(DOMAIN, "music_mode", _music_mode, {})
    hub.register_service(DOMAIN, "flash_all", _flash_all, {})
    hub.register_service(DOMAIN, "clear", _clear, {})
