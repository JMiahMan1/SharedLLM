# services/execution/handlers/security.py
import logging
from .. import ha_client
from ..schemas import UserContext, ExecutionResult
from pydantic import BaseModel
from typing import Literal

log = logging.getLogger("execution.security")

class SecurityRequest(BaseModel):
    user_context: UserContext
    entity_id: str
    action: Literal["lock", "unlock", "open", "close", "status"]

async def handle_security(req: SecurityRequest) -> ExecutionResult:
    ctx = req.user_context
    log.info(f"[security] user={ctx.user} entity={req.entity_id} action={req.action}")

    if req.action == "status":
        state = await ha_client.get_state(ctx.ha_url, ctx.ha_token, req.entity_id)
        if state:
            return ExecutionResult(
                status="SUCCESS", 
                message=f"The {req.entity_id} is {state.get('state')}.", 
                service="security",
                detail=state
            )
        return ExecutionResult(status="FAILURE", message=f"Could not get state for {req.entity_id}", service="security")

    domain = req.entity_id.split(".")[0]
    # Map high-level actions to domain-specific services
    ha_service_map = {
        "open": "open_cover",
        "close": "close_cover",
    }
    service = ha_service_map.get(req.action, req.action)

    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token,
        domain, service,
        req.entity_id
    )
    
    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Security action '{req.action}' executed on {req.entity_id}.", service="security")
    return ExecutionResult(status="FAILURE", message=f"Security action failed: {result.get('error')}", service="security", detail=result)
