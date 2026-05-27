# services/execution/handlers/climate.py
import logging
try:
    import ha_client
    from schemas import UserContext, ExecutionResult
except ImportError:
    from .. import ha_client
    from ..schemas import UserContext, ExecutionResult
from pydantic import BaseModel

log = logging.getLogger("execution.climate")

class ClimateRequest(BaseModel):
    user_context: UserContext
    entity_id: str
    temperature: float

async def handle_climate(req: ClimateRequest) -> ExecutionResult:
    ctx = req.user_context
    log.info(f"[climate] user={ctx.user} entity={req.entity_id} temp={req.temperature}")

    # 1. AUTHORIZATION CHECK
    if not ha_client.authorize_action(ctx.model_dump(), "climate", "set_temperature"):
        return ExecutionResult(
            status="FAILURE",
            message=f"Access Denied: You are not authorized to set temperature on {req.entity_id}. Admin privileges required.",
            service="climate"
        )

    assert ctx.ha_url is not None and ctx.ha_token is not None
    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token,
        "climate", "set_temperature",
        req.entity_id, {"temperature": req.temperature},
    )
    
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Temperature set to {req.temperature} on {req.entity_id}.", service="climate")
    return ExecutionResult(status="FAILURE", message=f"Climate command failed: {result.get('error')}", service="climate", detail=result)
