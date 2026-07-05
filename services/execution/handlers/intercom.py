"""
Handler for Household Intercom System (Section 3.16).
Manages intercom sessions, broadcasts, and announcements.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import aiohttp

from services.execution.schemas import ExecutionResult, UserContext

log = logging.getLogger("execution/intercom")


async def _get_identity_session() -> aiohttp.ClientSession:
    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET
    return aiohttp.ClientSession(
        base_url=IDENTITY_SVC_URL,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        timeout=aiohttp.ClientTimeout(total=10.0),
    )


async def _call_identity(method: str, path: str, json_data: Optional[Dict] = None) -> Dict:
    async with await _get_identity_session() as client:
        async with client.request(method, path, json=json_data) as resp:
            resp.raise_for_status()
            return await resp.json()


async def _resolve_user_room(user_id: str) -> Optional[str]:
    """Resolve user's current room via ESPresense presence data."""
    try:
        from services.execution.presence import get_presence_tracker
        tracker = get_presence_tracker()
        presence = await tracker.get_user_presence(user_id)
        if presence and presence.get("room") and presence.get("room") != "unknown":
            return presence["room"]
    except Exception as e:
        log.warning(f"[intercom] Presence lookup failed for {user_id}: {e}")
    return None


# ─── Two-Way Intercom Sessions ────────────────────────────────────────────────

async def handle_intercom_start(req, user_context: UserContext) -> ExecutionResult:
    """Start a two-way intercom session."""
    try:
        target_room = getattr(req, "target_room", None)
        target_user_id = getattr(req, "target_user_id", None)

        # Resolve target room via presence if not explicitly provided
        if not target_room and target_user_id:
            target_room = await _resolve_user_room(target_user_id)
            if target_room:
                log.info(f"[intercom] Resolved {target_user_id} -> room: {target_room}")

        payload = {
            "caller_user_id": req.caller_user_id or user_context.user,
            "target_user_id": target_user_id,
            "target_room": target_room,
            "target_entity_ids": getattr(req, "target_entity_ids", []),
            "session_type": getattr(req, "session_type", "twoway"),
        }
        session_data = await _call_identity("POST", "/api/intercom/sessions", payload)
        return ExecutionResult(
            status="SUCCESS",
            message=f"Intercom session started: {session_data.get('session_id', 'unknown')}",
            service="intercom",
            detail=session_data,
        )
    except Exception as e:
        log.error(f"Intercom start failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="intercom")


async def handle_intercom_end(req) -> ExecutionResult:
    """End an active intercom session."""
    try:
        session_id = getattr(req, "session_id", None)
        if not session_id:
            return ExecutionResult(status="FAILURE", message="session_id is required", service="intercom")
        await _call_identity("DELETE", f"/api/intercom/sessions/{session_id}")
        return ExecutionResult(
            status="SUCCESS",
            message=f"Intercom session '{session_id}' ended",
            service="intercom",
        )
    except Exception as e:
        log.error(f"Intercom end failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="intercom")


async def handle_intercom_list_sessions() -> ExecutionResult:
    """List active intercom sessions."""
    try:
        sessions = await _call_identity("GET", "/api/intercom/sessions")
        return ExecutionResult(
            status="SUCCESS",
            message="Intercom sessions retrieved",
            service="intercom",
            detail={"sessions": sessions},
        )
    except Exception as e:
        log.error(f"Intercom list sessions failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="intercom")


# ─── Broadcast / PA System ────────────────────────────────────────────────────

async def handle_intercom_broadcast(req, user_context: UserContext) -> ExecutionResult:
    """Broadcast a message to target devices/rooms."""
    try:
        payload = {
            "message": req.message,
            "target_entity_ids": getattr(req, "target_entity_ids", []),
            "target_rooms": getattr(req, "target_rooms", []),
            "volume": getattr(req, "volume", None),
            "tts_engine": getattr(req, "tts_engine", None),
            "voice": getattr(req, "voice", None),
            "caller_user_id": user_context.user,
        }
        result = await _call_identity("POST", "/api/intercom/broadcast", payload)
        return ExecutionResult(
            status="SUCCESS",
            message=f"Broadcast sent to {result.get('targets_count', 0)} devices",
            service="intercom",
            detail=result,
        )
    except Exception as e:
        log.error(f"Intercom broadcast failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="intercom")


# ─── TV/Smart Speaker Announcements ───────────────────────────────────────────

async def handle_intercom_announcement(req, user_context: UserContext) -> ExecutionResult:
    """Send a one-way announcement to TVs or smart speakers."""
    try:
        payload = {
            "message": req.message,
            "target_devices": getattr(req, "target_devices", []),
            "overlay_text": getattr(req, "overlay_text", None),
            "caller_user_id": user_context.user,
        }
        result = await _call_identity("POST", "/api/intercom/announce", payload)
        return ExecutionResult(
            status="SUCCESS",
            message=f"Announcement sent to {result.get('targets_count', 0)} devices",
            service="intercom",
            detail=result,
        )
    except Exception as e:
        log.error(f"Intercom announcement failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="intercom")


# ─── Intercom Configuration ───────────────────────────────────────────────────

async def handle_intercom_config() -> ExecutionResult:
    """Get intercom configuration."""
    try:
        config = await _call_identity("GET", "/api/intercom/config")
        return ExecutionResult(
            status="SUCCESS",
            message="Intercom configuration retrieved",
            service="intercom",
            detail=config,
        )
    except Exception as e:
        log.error(f"Intercom config retrieval failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="intercom")


async def handle_intercom_update_config(req) -> ExecutionResult:
    """Update intercom configuration."""
    try:
        payload = {
            "default_tts_engine": getattr(req, "default_tts_engine", None),
            "default_voice": getattr(req, "default_voice", None),
            "default_volume": getattr(req, "default_volume", None),
            "enable_espresense_routing": getattr(req, "enable_espresense_routing", True),
        }
        config = await _call_identity("PATCH", "/api/intercom/config", payload)
        return ExecutionResult(
            status="SUCCESS",
            message="Intercom configuration updated",
            service="intercom",
            detail=config,
        )
    except Exception as e:
        log.error(f"Intercom config update failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="intercom")
