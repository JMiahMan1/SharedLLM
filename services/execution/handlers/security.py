# services/execution/handlers/security.py
import logging

try:
    import ha_client
    from schemas import ExecutionResult, UserContext
except ImportError:
    from .. import ha_client
    from ..schemas import ExecutionResult, UserContext
from typing import Literal

from pydantic import BaseModel

log = logging.getLogger("execution.security")

class SecurityRequest(BaseModel):
    user_context: UserContext
    entity_id: str
    action: Literal["lock", "unlock", "open", "close", "status"]

async def _hub_context(ctx: UserContext, entity_id: str, service: str) -> UserContext | ExecutionResult:
    """The context with the hub login to use: the caller's own, else the hub's.

    On the hub's login the caller must be allowed this entity (it used to be an
    assertion, a 500 for everyone without a stored Home Assistant token).
    """
    from services.execution import hardware_router
    from services.execution.entity_access import can_view, load_entity_permissions, verify_entity_access

    hub_ctx = hardware_router.with_hub_login(ctx)
    if hub_ctx is None:
        return ExecutionResult(status="FAILURE", message=hardware_router.NO_HUB_MESSAGE, service="security")
    if hub_ctx is not ctx:
        if service == "status":
            allowed = can_view(await load_entity_permissions(ctx.user, ctx.is_admin), entity_id)
        else:
            allowed = await verify_entity_access(ctx, entity_id, service=service)
        if not allowed:
            return ExecutionResult(status="FAILURE", service="security",
                                   message=f"Access Denied: {ctx.user} may not use {entity_id}.")
    return hub_ctx


async def handle_security(req: SecurityRequest) -> ExecutionResult:
    ctx = req.user_context
    # Resolve and sanitize entity_id based on prefix or default to 'lock'/'cover'
    domain_guess = req.entity_id.split(".")[0] if "." in req.entity_id else "lock"
    full_entity_id = ha_client.sanitize_entity_id(domain_guess, req.entity_id)

    log.info(f"[security] user={ctx.user} entity={full_entity_id} action={req.action} (original={req.entity_id})")

    if req.action == "status":
        hub = await _hub_context(ctx, full_entity_id, "status")
        if isinstance(hub, ExecutionResult):
            return hub
        ctx = hub
        assert ctx.ha_url is not None and ctx.ha_token is not None
        state = await ha_client.get_state(ctx.ha_url, ctx.ha_token, full_entity_id)
        if state:
            return ExecutionResult(
                status="SUCCESS",
                message=f"The {full_entity_id} is {state.get('state')}.",
                service="security",
                detail=state
            )
        return ExecutionResult(status="FAILURE", message=f"Could not get state for {full_entity_id}", service="security")

    domain = full_entity_id.split(".")[0]
    # Map high-level actions to domain-specific services
    ha_service_map = {
        "open": "open_cover",
        "close": "close_cover",
    }
    service = ha_service_map.get(req.action, req.action)

    # 1. AUTHORIZATION CHECK
    if not ha_client.authorize_action(ctx.model_dump(), domain, service):
        return ExecutionResult(
            status="FAILURE",
            message=f"Access Denied: You are not authorized to perform '{req.action}' on {full_entity_id}. Admin privileges required.",
            service="security"
        )

    # 2. Voice and chat may only act on what the hub exposes to them (raises with the reason).
    from services.execution import voice_exposure

    await voice_exposure.require_exposed(ctx, full_entity_id, service=service)

    hub = await _hub_context(ctx, full_entity_id, service)
    if isinstance(hub, ExecutionResult):
        return hub
    ctx = hub
    assert ctx.ha_url is not None and ctx.ha_token is not None
    result = await ha_client.call_service(
        ctx.ha_url, ctx.ha_token,
        domain, service,
        full_entity_id
    )

    if result.get("ok"):
        return ExecutionResult(status="SUCCESS", message=f"Security action '{req.action}' executed on {req.entity_id}.", service="security")
    return ExecutionResult(status="FAILURE", message=f"Security action failed: {result.get('error')}", service="security", detail=result)
