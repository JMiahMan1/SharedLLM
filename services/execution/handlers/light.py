# services/execution/handlers/light.py
import logging
from .. import ha_client
from ..schemas import LightControlRequest, ExecutionResult

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

    # Auto-detect domain if needed, though light control usually uses 'light'
    domain = req.entity_id.split(".")[0] if "." in req.entity_id else "light"
    
    # Force 'light' domain for turn_on/off if it's a light entity
    # but allow switches to work too
    ha_domain = "light" if domain == "light" else domain

    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token,
        ha_domain, req.action,
        req.entity_id, service_data or None,
    )
    
    if result.get("ok"):
        return ExecutionResult(
            status="SUCCESS", 
            message=f"Command '{req.action}' executed on {req.entity_id}.", 
            service="light_control"
        )
    return ExecutionResult(
        status="FAILURE", 
        message=f"Light command failed: {result.get('error')}", 
        service="light_control",
        detail=result
    )
