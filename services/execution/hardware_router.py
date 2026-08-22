# services/execution/hardware_router.py
"""Unified hardware routing between Home Assistant and direct ESPHome.

One interaction path for device control: callers issue standard HA-style
service calls and the router decides how they reach the hardware.

Policy (HA-first, chosen by the user):
1. HA is attempted first for every call — existing behavior is unchanged
   while HA is healthy.
2. Only when HA is *unreachable* (connection error / not configured) do we
   fall back to a direct ESPHome native-API call, provided the target
   entity belongs to a configured ESPHome device:
   - explicit ``ha_entity_id`` mapping in the ``esphome_devices`` setting
     wins, otherwise
   - object-id/domain correlation against each device's entity list
     (HA names ESPHome-backed entities ``<domain>.<object_id>``).
3. HA answering with an HTTP error (4xx/5xx) is NOT an outage: it is a
   real answer (bad entity, auth, ...) and never triggers fallback.
4. Tasks that cannot be translated to a native ESPHome command (scenes,
   scripts, automations, toggle without a readable state, ...) fail with
   a clear message instead of being misrouted.

Every result carries ``detail.route`` ("ha" or "esphome_direct") plus the
device used, so logs and the UI show which path served the request.
"""

import logging

import aiohttp

try:
    import esphome_client
    from schemas import ExecutionResult
except ImportError:
    from . import esphome_client  # type: ignore[attr-defined]
    from .schemas import ExecutionResult  # type: ignore[attr-defined]

log = logging.getLogger("execution.hardware_router")


def _is_ha_unreachable(ha_result: dict) -> bool:
    """True only when HA could not be reached at all (vs answered with an error)."""
    if ha_result.get("ok"):
        return False
    return "unreachable" in str(ha_result.get("error", "")).lower()


def _translate_ha_call(domain: str, service: str, service_data: dict | None) -> dict | None:
    """Translate an HA service call into direct-ESPHome entity params.

    Returns params for esphome_client.call_entity, or None when this task
    has no native translation (caller must not misroute it).
    """
    data = service_data or {}

    if domain == "light":
        if service == "turn_on":
            params: dict = {"state": True}
            if data.get("brightness_pct") is not None:
                params["brightness_pct"] = data["brightness_pct"]
            if data.get("brightness") is not None:
                params["brightness_pct"] = round(float(data["brightness"]) * 100)
            if data.get("rgb_color"):
                params["rgb"] = list(data["rgb_color"])
            return params
        if service == "turn_off":
            return {"state": False}
        # light.toggle needs current state; with HA down there is none to read.
        return None

    if domain == "switch":
        if service == "turn_on":
            return {"state": True}
        if service == "turn_off":
            return {"state": False}
        return None

    if domain == "fan":
        if service in ("turn_on", "turn_off"):
            params = {"state": service == "turn_on"}
            if data.get("percentage") is not None:
                params["speed_level"] = round(float(data["percentage"]) / 25)
            return params
        return None

    if domain == "cover":
        if service == "open_cover":
            return {"position": 100}
        if service == "close_cover":
            return {"position": 0}
        if service == "stop_cover":
            return {"stop": True}
        if service == "set_cover_position":
            if data.get("position") is None:
                return None
            return {"position": data["position"]}
        return None

    if domain == "button" and service == "press":
        return {}

    if domain == "climate" and service == "set_temperature":
        climate_params: dict = {}
        if data.get("temperature") is not None:
            climate_params["target_temperature"] = data["temperature"]
        if data.get("hvac_mode"):
            climate_params["mode"] = data["hvac_mode"]
        return climate_params or None

    if domain == "siren":
        if service in ("turn_on", "turn_off"):
            return {"state": service == "turn_on"}
        return None

    if domain == "select" and service == "select_option":
        return {"option": data.get("option")} if data.get("option") else None

    if domain == "number" and service == "set_value":
        return {"value": data.get("value")} if data.get("value") is not None else None

    if domain == "media_player":
        command_map = {
            "media_play": "PLAY",
            "media_pause": "PAUSE",
            "media_stop": "STOP",
            "media_play_pause": "PLAY",
        }
        if service == "volume_set" and data.get("volume_level") is not None:
            return {"volume": data["volume_level"]}
        if service == "play_media" and data.get("media_content_id"):
            return {"media_url": data["media_content_id"], "announcement": bool(data.get("announce"))}
        if service in command_map:
            return {"command": command_map[service]}
        return None

    return None


async def _direct_esphome_fallback(
    domain: str,
    service: str,
    entity_id: str,
    service_data: dict | None,
    ha_error: str,
    service_name: str,
) -> ExecutionResult:
    """Attempt the same task straight against the ESPHome device."""
    try:
        device_name = await esphome_client.find_device_for_ha_entity(entity_id)
        if device_name is None:
            return ExecutionResult(
                status="FAILURE",
                message=(
                    f"Home Assistant is unreachable ({ha_error}) and '{entity_id}' "
                    "is not a configured ESPHome device, so there is no direct "
                    "route. Add it under Admin > Hardware."
                ),
                service=service_name,
                detail={"route": "none", "ha_error": ha_error},
            )
        params = _translate_ha_call(domain, service, service_data)
        if params is None:
            return ExecutionResult(
                status="FAILURE",
                message=(
                    f"Home Assistant is unreachable ({ha_error}) and "
                    f"'{domain}.{service}' cannot be translated to a direct "
                    "ESPHome command. Restore HA access to run this task."
                ),
                service=service_name,
                detail={"route": "none", "device": device_name, "ha_error": ha_error},
            )
        ha_domain = entity_id.split(".", 1)[0]
        result = await esphome_client.call_entity(
            device_name, entity_id.split(".", 1)[1], params
        )
        if result.get("domain") != ha_domain:
            log.warning(
                f"[router] entity domain mismatch: HA '{entity_id}' vs device "
                f"entity domain '{result.get('domain')}'"
            )
        log.info(
            f"[router] {domain}.{service} -> {entity_id} served via ESPHome "
            f"direct (device='{device_name}', HA unreachable)"
        )
        return ExecutionResult(
            status="SUCCESS",
            message=(
                f"{domain}.{service} executed via direct ESPHome connection to "
                f"'{device_name}' (Home Assistant was unreachable)."
            ),
            service=service_name,
            detail={
                "route": "esphome_direct",
                "device": device_name,
                "entity": result,
            },
        )
    except esphome_client.EsphomeConfigError as e:
        return ExecutionResult(
            status="FAILURE",
            message=(
                f"Home Assistant is unreachable ({ha_error}) and the direct "
                f"ESPHome route failed too: {e}"
            ),
            service=service_name,
            detail={"route": "none", "ha_error": ha_error},
        )


async def execute_device_command(
    ctx,
    domain: str,
    service: str,
    entity_id: str,
    service_data: dict | None = None,
    service_name: str = "ha_service",
) -> ExecutionResult:
    """Route one device command: HA first, direct ESPHome when HA is down."""
    assert ctx.ha_url is not None and ctx.ha_token is not None
    ha_result = await _call_ha(ctx, domain, service, entity_id, service_data)
    if ha_result.get("ok"):
        return ExecutionResult(
            status="SUCCESS",
            message=f"{domain}.{service} executed.",
            service=service_name,
            detail={"route": "ha"},
        )

    if _is_ha_unreachable(ha_result):
        ha_error = str(ha_result.get("error", "unknown error"))
        log.warning(f"[router] HA unreachable ({ha_error}); trying ESPHome direct")
        return await _direct_esphome_fallback(
            domain, service, entity_id, service_data, ha_error, service_name
        )

    return ExecutionResult(
        status="FAILURE",
        message=f"Service call failed: {ha_result.get('error')}",
        service=service_name,
        detail={"route": "ha", **ha_result},
    )


async def _call_ha(ctx, domain: str, service: str, entity_id: str, service_data: dict | None) -> dict:
    """Call HA, tolerating a missing client session dependency at import time."""
    from services.execution import ha_client

    try:
        return await ha_client.call_service(
            ctx.ha_url or "", ctx.ha_token or "", domain, service, entity_id, service_data
        )
    except aiohttp.ClientError as e:
        # ha_client already converts these, but keep the router self-sufficient
        return {"ok": False, "error": f"Home Assistant is unreachable: {e}"}
