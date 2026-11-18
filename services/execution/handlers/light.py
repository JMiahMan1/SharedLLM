# services/execution/handlers/light.py
import asyncio
import logging
import time

try:
    import ha_client
    from schemas import ExecutionResult, LightControlRequest
except ImportError:
    from .. import ha_client
    from ..schemas import ExecutionResult, LightControlRequest

try:
    import hardware_router
except ImportError:
    from .. import hardware_router  # type: ignore[attr-defined]

log = logging.getLogger("execution.light")

#: How long to watch a light after a command before saying what it did. A hub
#: answers a service call before a bulb has changed (a Zigbee or Wi-Fi bulb takes
#: a moment), so "executed" was said before the light came on, or when it never
#: did. Short enough that a voice answer still feels immediate.
CONFIRM_WITHIN_S = 4.0
CONFIRM_POLL_S = 0.4

ACTIVE_STATES = {"on", "playing", "idle", "standby", "home", "cooling", "heating", "drying", "cleaning"}
INACTIVE_STATES = {"off", "unavailable", "unknown", "not_home"}

async def handle_light(req: LightControlRequest) -> ExecutionResult:
    ctx = req.user_context
    log.info(f"[light] user={ctx.user} entity={req.entity_id} action={req.action}")

    # Resolve and sanitize entity_id
    full_entity_id = ha_client.sanitize_entity_id("light", req.entity_id)
    domain = full_entity_id.split(".")[0]

    log.info(f"[light] user={ctx.user} (admin={ctx.is_admin}) entity={full_entity_id} action={req.action} (original={req.entity_id})")

    # 1. AUTHORIZATION CHECK
    if not ha_client.authorize_action(ctx.model_dump(), domain, req.action):
        return ExecutionResult(
            status="FAILURE",
            message=f"Access Denied: You are not authorized to perform '{req.action}' on {full_entity_id}. Admin privileges required.",
            service="light_control"
        )

    # 2. HUB LOGIN & STATE CHECK
    #    The caller's own login, else the hub's (never another person's token).
    #    The route checked the caller may control this entity before we got here.
    hub_ctx = hardware_router.with_hub_login(ctx)
    if hub_ctx is None:
        return ExecutionResult(status="FAILURE", message=hardware_router.NO_HUB_MESSAGE, service="light_control")
    ctx = hub_ctx

    current = await ha_client.get_state(ctx.ha_url, ctx.ha_token, full_entity_id)
    friendly = _spoken_name(full_entity_id)
    if current:
        current_state = current.get("state", "").lower()
        friendly = current.get("attributes", {}).get("friendly_name", full_entity_id)

        if current_state == "unavailable":
            return ExecutionResult(
                status="FAILURE",
                message=f"The light '{friendly}' ({full_entity_id}) is currently unavailable on the home hub. The device may be powered off, disconnected from WiFi, or not responding to MQTT. Please check the physical device and its network connection.",
                service="light_control"
            )
        if current_state == "unknown":
            return ExecutionResult(
                status="FAILURE",
                message=f"The light '{friendly}' ({full_entity_id}) has an unknown state on the home hub. It may be initializing or misconfigured.",
                service="light_control"
            )

        has_attributes = (
            req.brightness_pct is not None
            or req.color_temp is not None
            or req.rgb_color is not None
        )
        if req.action in ("turn_on", "turn_off"):
            target_state = "on" if req.action == "turn_on" else "off"
            if current_state == target_state and not (req.action == "turn_on" and has_attributes):
                return ExecutionResult(
                    status="SUCCESS",
                    message=f"{friendly} is already {target_state}.",
                    service="light_control"
                )

    service_data: dict = {}
    if req.brightness_pct is not None:
        service_data["brightness_pct"] = req.brightness_pct
    if req.color_temp is not None:
        service_data["color_temp"] = req.color_temp
    if req.rgb_color is not None:
        service_data["rgb_color"] = list(req.rgb_color)

    # Unified hardware routing: HA first, direct ESPHome when HA unreachable.
    result = await hardware_router.execute_device_command(
        ctx, domain, req.action, full_entity_id, service_data or None,
        service_name="light_control",
    )
    if result.status != "SUCCESS" or req.action not in ("turn_on", "turn_off"):
        return result
    target = "on" if req.action == "turn_on" else "off"
    if (result.detail or {}).get("route") != "ha" or current is None:
        # Sent around the hub, or a light whose state cannot be read: say what
        # was done, in words (never the hub's service name).
        return ExecutionResult(status="SUCCESS", message=f"Turned {target} {friendly}.",
                               service=result.service, detail={**(result.detail or {}), "confirmed": False})
    return await _confirmed(ctx, full_entity_id, friendly, req, result)


def _spoken_name(entity_id: str) -> str:
    """What to call a device when the hub gives no friendly name: light.hall_lamp -> hall lamp."""
    return entity_id.split(".", 1)[-1].replace("_", " ")


async def _confirmed(ctx, entity_id: str, friendly: str, req: LightControlRequest, sent: ExecutionResult) -> ExecutionResult:
    """Watch the light until it shows the new state, then say that state (never the command).

    A light that has not changed within ``CONFIRM_WITHIN_S`` is reported as it is,
    so Jarvis never says it turned on a light that is still off.
    """
    target = "on" if req.action == "turn_on" else "off"
    deadline = time.monotonic() + CONFIRM_WITHIN_S
    state = None
    while True:
        current = await ha_client.get_state(ctx.ha_url, ctx.ha_token, entity_id)
        if current:
            state = str(current.get("state", "")).lower()
            friendly = current.get("attributes", {}).get("friendly_name") or friendly
            if state == target:
                level = _brightness_pct(current) if target == "on" else None
                said = f"{friendly} is on at {level}%." if level is not None and req.brightness_pct is not None else f"{friendly} is {target}."
                return ExecutionResult(status="SUCCESS", message=said, service=sent.service,
                                       detail={**(sent.detail or {}), "confirmed": True, "state": state})
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(CONFIRM_POLL_S)
    if state == "unavailable":
        said = f"I asked {friendly} to turn {target}, but it is not responding."
    elif state:
        said = f"I asked {friendly} to turn {target}, but it still shows {state}."
    else:
        said = f"I asked {friendly} to turn {target}, but I could not read it back to check."
    log.warning("[light] %s did not show %s within %.0fs (state=%s)", entity_id, target, CONFIRM_WITHIN_S, state)
    return ExecutionResult(status="PARTIAL", message=said, service=sent.service,
                           detail={**(sent.detail or {}), "confirmed": False, "state": state})


def _brightness_pct(state: dict) -> int | None:
    raw = (state.get("attributes") or {}).get("brightness")
    if not isinstance(raw, (int, float)):
        return None
    return round(raw * 100 / 255)
