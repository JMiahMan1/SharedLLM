"""
Handler for device groups, light clusters, and light patterns.
Manages CRUD operations via the Identity service database.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import httpx

from schemas import ExecutionResult, UserContext

log = logging.getLogger("execution.groups")


async def _get_identity_session() -> httpx.AsyncClient:
    """Create an authenticated session to the Identity service."""
    from config import IDENTITY_SVC_URL, INTERNAL_SECRET
    return httpx.AsyncClient(
        base_url=IDENTITY_SVC_URL,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        timeout=10.0,
    )


async def _call_identity(method: str, path: str, json_data: Optional[Dict] = None) -> Dict:
    """Make a request to the Identity service."""
    async with await _get_identity_session() as client:
        resp = await client.request(method, path, json=json_data)
        resp.raise_for_status()
        return resp.json()


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

async def handle_light_pattern(req) -> ExecutionResult:
    """Handle light pattern CRUD operations."""
    try:
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
