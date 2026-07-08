# services/execution/handlers/light.py
import logging

try:
    import ha_client
    from schemas import ExecutionResult, LightControlRequest
except ImportError:
    from .. import ha_client
    from ..schemas import ExecutionResult, LightControlRequest

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

    # 2. STATE CHECK — avoid redundant commands
    if req.action in ("turn_on", "turn_off"):
        target_state = "on" if req.action == "turn_on" else "off"
        assert ctx.ha_url is not None and ctx.ha_token is not None
        current = await ha_client.get_state(ctx.ha_url, ctx.ha_token, full_entity_id)
        if current:
            current_state = current.get("state", "").lower()
            if current_state == target_state:
                friendly = current.get("attributes", {}).get("friendly_name", full_entity_id)
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

    assert ctx.ha_url is not None and ctx.ha_token is not None
    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token,
        domain, req.action,
        full_entity_id, service_data or None,
    )

    log.info(f"[light] RESULT: {result.get('ok')} | entity={full_entity_id} | error={result.get('error')}")

    if result.get("ok"):
        return ExecutionResult(
            status="SUCCESS",
            message=f"Command '{req.action}' executed on {full_entity_id}.",
            service="light_control"
        )
    return ExecutionResult(
        status="FAILURE",
        message=f"Light command failed: {result.get('error')}",
        service="light_control",
        detail=result
    )
