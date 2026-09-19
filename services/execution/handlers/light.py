# services/execution/handlers/light.py
import logging

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

    # 2. CREDENTIAL RESOLUTION & STATE CHECK
    if not ctx.ha_url or not ctx.ha_token:
        try:
            from ..main import resolve_first_user
        except Exception:
            try:
                from main import resolve_first_user
            except Exception:
                resolve_first_user = None
        if resolve_first_user:
            creds = await resolve_first_user()
            if creds:
                ctx.ha_url = ctx.ha_url or creds.get("ha_url")
                ctx.ha_token = ctx.ha_token or creds.get("ha_token")

    if not ctx.ha_url or not ctx.ha_token:
        return ExecutionResult(
            status="FAILURE",
            message="Home Assistant URL or token not configured.",
            service="light_control"
        )

    current = await ha_client.get_state(ctx.ha_url, ctx.ha_token, full_entity_id)
    if current:
        current_state = current.get("state", "").lower()
        friendly = current.get("attributes", {}).get("friendly_name", full_entity_id)

        if current_state == "unavailable":
            return ExecutionResult(
                status="FAILURE",
                message=f"The light '{friendly}' ({full_entity_id}) is currently unavailable in Home Assistant. The device may be powered off, disconnected from WiFi, or not responding to MQTT. Please check the physical device and its network connection.",
                service="light_control"
            )
        if current_state == "unknown":
            return ExecutionResult(
                status="FAILURE",
                message=f"The light '{friendly}' ({full_entity_id}) has an unknown state in Home Assistant. It may be initializing or misconfigured.",
                service="light_control"
            )

        if req.action in ("turn_on", "turn_off"):
            target_state = "on" if req.action == "turn_on" else "off"
            if current_state == target_state:
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
    return await hardware_router.execute_device_command(
        ctx, domain, req.action, full_entity_id, service_data or None,
        service_name="light_control",
    )
