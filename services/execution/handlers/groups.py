"""
Handler for device groups, light clusters, and light patterns.
Manages CRUD operations via the Identity service database.
"""
from __future__ import annotations

import logging

import aiohttp

try:
    from schemas import ExecutionResult, UserContext
except ImportError:
    from ..schemas import ExecutionResult, UserContext

log = logging.getLogger("execution.groups")


async def _call_identity(method: str, path: str, json_data: dict | None = None) -> dict:
    """Make a request to the Identity service."""
    from services.common.http import get_client
    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

    url = f"{IDENTITY_SVC_URL.rstrip('/')}{path}"
    headers = {"X-Internal-Secret": INTERNAL_SECRET}
    async with get_client() as client, client.request(
        method, url, json=json_data, headers=headers, timeout=aiohttp.ClientTimeout(total=10.0)
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


# ─── Media Groups ──────────────────────────────────────────────────────────────

async def handle_media_group(req, user_context: UserContext) -> ExecutionResult:
    """Handle media group CRUD operations."""
    try:
        if req.action == "list":
            groups = await _call_identity("GET", "/api/groups/media")
            return ExecutionResult(status="SUCCESS", message="Media groups retrieved", service="media_groups", detail={"groups": groups})

        if req.action == "create":
            payload = {
                "group_id": req.group_id,
                "group_name": req.group_name or req.group_id,
                "member_entity_ids": req.member_entity_ids or [],
                "scope": req.scope,
                "owner_user_id": user_context.user,
            }
            await _call_identity("POST", "/api/groups/media", payload)
            return ExecutionResult(status="SUCCESS", message=f"Media group '{req.group_id}' created", service="media_groups")

        if req.action == "delete":
            await _call_identity("DELETE", f"/api/groups/media/{req.group_id}")
            return ExecutionResult(status="SUCCESS", message=f"Media group '{req.group_id}' deleted", service="media_groups")

        if req.action == "add_member":
            await _call_identity("POST", f"/api/groups/media/{req.group_id}/members", {
                "entity_ids": req.member_entity_ids or [],
            })
            return ExecutionResult(status="SUCCESS", message=f"Members added to '{req.group_id}'", service="media_groups")

        if req.action == "remove_member":
            await _call_identity("DELETE", f"/api/groups/media/{req.group_id}/members", {
                "entity_ids": req.member_entity_ids or [],
            })
            return ExecutionResult(status="SUCCESS", message=f"Members removed from '{req.group_id}'", service="media_groups")

        return ExecutionResult(status="FAILURE", message=f"Unknown action: {req.action}", service="media_groups")
    except Exception as e:
        log.error(f"Media group operation failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="media_groups")


# ─── Light Clusters ────────────────────────────────────────────────────────────

async def handle_light_cluster(req, user_context: UserContext) -> ExecutionResult:
    """Handle light cluster CRUD operations."""
    try:
        if req.action == "list":
            clusters = await _call_identity("GET", "/api/groups/lights")
            return ExecutionResult(status="SUCCESS", message="Light clusters retrieved", service="light_clusters", detail={"clusters": clusters})

        if req.action == "create":
            payload = {
                "cluster_id": req.cluster_id,
                "cluster_name": req.cluster_name or req.cluster_id,
                "member_entity_ids": req.member_entity_ids or [],
                "room": req.room,
                "scope": req.scope,
                "owner_user_id": user_context.user,
            }
            await _call_identity("POST", "/api/groups/lights", payload)
            return ExecutionResult(status="SUCCESS", message=f"Light cluster '{req.cluster_id}' created", service="light_clusters")

        if req.action == "delete":
            await _call_identity("DELETE", f"/api/groups/lights/{req.cluster_id}")
            return ExecutionResult(status="SUCCESS", message=f"Light cluster '{req.cluster_id}' deleted", service="light_clusters")

        if req.action == "add_member":
            await _call_identity("POST", f"/api/groups/lights/{req.cluster_id}/members", {
                "entity_ids": req.member_entity_ids or [],
            })
            return ExecutionResult(status="SUCCESS", message=f"Members added to '{req.cluster_id}'", service="light_clusters")

        if req.action == "remove_member":
            await _call_identity("DELETE", f"/api/groups/lights/{req.cluster_id}/members", {
                "entity_ids": req.member_entity_ids or [],
            })
            return ExecutionResult(status="SUCCESS", message=f"Members removed from '{req.cluster_id}'", service="light_clusters")

        return ExecutionResult(status="FAILURE", message=f"Unknown action: {req.action}", service="light_clusters")
    except Exception as e:
        log.error(f"Light cluster operation failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="light_clusters")


# ─── Light Patterns ────────────────────────────────────────────────────────────

def _find(items: list, key: str, *fields: str) -> dict | None:
    """The item whose id or name field equals ``key`` (case-insensitive)."""
    want = (key or "").strip().lower()
    for item in items or []:
        if any(str(item.get(f) or "").strip().lower() == want for f in fields):
            return item
    return None


async def _apply_light_pattern(req, ctx: UserContext) -> ExecutionResult:
    """Set a cluster's lights to a pattern.

    A step colours the cluster members at its ``positions`` (indexes into the
    cluster's member list; none means every member). A position past the end
    of a smaller cluster is skipped rather than wrapped, so two steps never
    fight over the same light.
    """
    try:
        import ha_client
        import hardware_router
        from schemas_groups import SYSTEM_DEFAULT_PATTERNS
    except ImportError:
        from .. import ha_client, hardware_router  # type: ignore[no-redef]
        from ..schemas_groups import SYSTEM_DEFAULT_PATTERNS  # type: ignore[no-redef]

    service = "light_patterns"
    stored = await _call_identity("GET", "/api/groups/patterns")
    pattern = _find(stored, req.pattern_id, "pattern_id", "pattern_name", "name") or _find(
        SYSTEM_DEFAULT_PATTERNS, req.pattern_id, "pattern_id", "pattern_name")
    if pattern is None:
        return ExecutionResult(status="FAILURE", message=f"No light pattern '{req.pattern_id}'.", service=service)
    cluster_key = req.cluster_id or pattern.get("cluster_id")
    if not cluster_key:
        return ExecutionResult(status="FAILURE", message="Choose a light cluster to apply the pattern to.",
                               service=service)
    cluster = _find(await _call_identity("GET", "/api/groups/lights"), cluster_key,
                    "cluster_id", "cluster_name", "name")
    if cluster is None:
        return ExecutionResult(status="FAILURE", message=f"No light cluster '{cluster_key}'.", service=service)
    members = [m for m in cluster.get("member_entity_ids") or [] if m]
    if not members:
        return ExecutionResult(status="FAILURE", message=f"Light cluster '{cluster_key}' has no lights.",
                               service=service)
    if not ha_client.authorize_action(ctx.model_dump(), "light", "turn_on"):
        return ExecutionResult(status="FAILURE", message="Access Denied: you may not control these lights.",
                               service=service)
    if not ctx.ha_url or not ctx.ha_token:
        return ExecutionResult(status="FAILURE", service=service,
                               message=f"Home Assistant URL or token not configured for user '{ctx.user}' "
                                       "(Identity -> Services).")

    transition_s = max(0, int(pattern.get("transition_ms", req.transition_ms))) / 1000
    targets: dict[str, dict] = {}
    for step in pattern.get("steps") or []:
        positions = step.get("positions") or list(range(len(members)))
        data = {"brightness_pct": int(step.get("brightness_pct", 100)), "transition": transition_s}
        if step.get("rgb"):
            data["rgb_color"] = list(step["rgb"])
        for pos in positions:
            if isinstance(pos, int) and 0 <= pos < len(members):
                targets[members[pos]] = data
    if not targets:
        return ExecutionResult(status="FAILURE", message="The pattern has no steps for this cluster's lights.",
                               service=service)

    failed: list[str] = []
    for entity_id, data in targets.items():
        result = await hardware_router.execute_device_command(
            ctx, "light", "turn_on", entity_id, data, service_name=service)
        if result.status != "SUCCESS":
            failed.append(f"{entity_id}: {result.message}")
    name = pattern.get("pattern_name") or pattern.get("pattern_id")
    if failed:
        return ExecutionResult(status="FAILURE" if len(failed) == len(targets) else "SUCCESS",
                               message=f"Applied {name} to {len(targets) - len(failed)} of {len(targets)} lights. "
                                       + "; ".join(failed),
                               service=service)
    return ExecutionResult(status="SUCCESS", message=f"Applied {name} to {len(targets)} lights.", service=service)


async def handle_light_pattern(req, user_context: UserContext | None = None) -> ExecutionResult:
    """Handle light pattern CRUD operations, and applying one."""
    try:
        if req.action == "apply":
            return await _apply_light_pattern(req, user_context or UserContext(user=""))
        if req.action == "list":
            patterns = await _call_identity("GET", "/api/groups/patterns")
            return ExecutionResult(status="SUCCESS", message="Light patterns retrieved", service="light_patterns", detail={"patterns": patterns})

        if req.action == "create":
            steps_data = []
            if req.steps:
                steps_data = [s.model_dump() for s in req.steps]
            payload = {
                "pattern_id": req.pattern_id,
                "pattern_name": req.pattern_name or req.pattern_id,
                "cluster_id": req.cluster_id,
                "steps": steps_data,
                "loop": req.loop,
                "transition_ms": req.transition_ms,
            }
            await _call_identity("POST", "/api/groups/patterns", payload)
            return ExecutionResult(status="SUCCESS", message=f"Light pattern '{req.pattern_id}' created", service="light_patterns")

        if req.action == "delete":
            await _call_identity("DELETE", f"/api/groups/patterns/{req.pattern_id}")
            return ExecutionResult(status="SUCCESS", message=f"Light pattern '{req.pattern_id}' deleted", service="light_patterns")

        if req.action == "update":
            steps_data = []
            if req.steps:
                steps_data = [s.model_dump() for s in req.steps]
            payload = {
                "pattern_name": req.pattern_name,
                "cluster_id": req.cluster_id,
                "steps": steps_data,
                "loop": req.loop,
                "transition_ms": req.transition_ms,
            }
            await _call_identity("PATCH", f"/api/groups/patterns/{req.pattern_id}", payload)
            return ExecutionResult(status="SUCCESS", message=f"Light pattern '{req.pattern_id}' updated", service="light_patterns")

        return ExecutionResult(status="FAILURE", message=f"Unknown action: {req.action}", service="light_patterns")
    except Exception as e:
        log.error(f"Light pattern operation failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="light_patterns")
