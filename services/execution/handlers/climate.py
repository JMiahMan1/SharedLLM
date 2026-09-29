# services/execution/handlers/climate.py
import logging

try:
    import ha_client
    from schemas import ExecutionResult, UserContext
except ImportError:
    from .. import ha_client
    from ..schemas import ExecutionResult, UserContext
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
    # Deliberately NOT ha_client.authorize_action: a setpoint is only as
    # sensitive as the entity itself, so the rule is "may this user control this
    # entity" — admin, system-default user, or the entity's own permit list.
    # Checking here (rather than only in the /execute/climate route) also closes
    # the composite night-mode path, which calls this handler directly.
    from services.execution.entity_access import verify_entity_access

    if not await verify_entity_access(ctx, req.entity_id):
        return ExecutionResult(
            status="FAILURE",
            message=(
                f"Access Denied: {ctx.user} is not permitted to set the "
                f"temperature on {req.entity_id}."
            ),
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
