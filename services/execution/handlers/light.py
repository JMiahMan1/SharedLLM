# services/execution/handlers/light.py
import logging
try:
    from .. import ha_client
    from ..schemas import LightControlRequest, ExecutionResult
except ImportError:
    import ha_client
    from schemas import LightControlRequest, ExecutionResult

log = logging.getLogger("execution.light")

async def handle_light(req: LightControlRequest) -> ExecutionResult:
    ctx = req.user_context
    log.info(f"[light] user={ctx.user} entity={req.entity_id} action={req.action}")

    service_data: dict = {}
    if req.brightness_pct is not None:
        service_data["brightness_pct"] = req.brightness_pct
    if req.color_temp is not None:
        service_data["color_temp"] = req.color_temp
    if req.rgb_color is not None:
        service_data["rgb_color"] = list(req.rgb_color)

    # Resolve and sanitize entity_id
    full_entity_id = ha_client.sanitize_entity_id("light", req.entity_id)
    domain = full_entity_id.split(".")[0]
    
    log.info(f"[light] user={ctx.user} (admin={ctx.is_admin}) entity={full_entity_id} action={req.action} (original={req.entity_id})")

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
