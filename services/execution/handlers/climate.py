# services/execution/handlers/climate.py
import logging
try:
    import ha_client
    from schemas import UserContext, ExecutionResult
except ImportError:
    import ha_client
    from schemas import UserContext, ExecutionResult
from pydantic import BaseModel

log = logging.getLogger("execution.climate")

class ClimateRequest(BaseModel):
    user_context: UserContext
    entity_id: str
    temperature: float

async def handle_climate(req: ClimateRequest) -> ExecutionResult:
    ctx = req.user_context
    log.info(f"[climate] user={ctx.user} entity={req.entity_id} temp={req.temperature}")

    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token,
        "climate", "set_temperature",
        req.entity_id, {"temperature": req.temperature},
    )
    
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Temperature set to {req.temperature} on {req.entity_id}.", service="climate")
    return ExecutionResult(status="FAILURE", message=f"Climate command failed: {result.get('error')}", service="climate", detail=result)
