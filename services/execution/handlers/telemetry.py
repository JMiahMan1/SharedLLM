"""
Handler for Device Telemetry Monitoring (Section 3.15).
Manages enrollment, snapshot ingestion, queries, and LLM pattern analysis.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import aiohttp

from services.execution.schemas import ExecutionResult, UserContext

log = logging.getLogger("execution/telemetry")


async def _get_identity_session() -> httpx.AsyncClient:
    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET
    return httpx.AsyncClient(
        base_url=IDENTITY_SVC_URL,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        timeout=10.0,
    )


async def _call_identity(method: str, path: str, json_data: Optional[Dict] = None) -> Dict:
    async with await _get_identity_session() as client:
        resp = await client.request(method, path, json=json_data)
        resp.raise_for_status()
        return resp.json()


# ─── Enrollment ────────────────────────────────────────────────────────────────

async def handle_telemetry_enroll(req, user_context: UserContext) -> ExecutionResult:
    """Enroll a device in telemetry monitoring."""
    try:
        payload = {
            "entity_id": req.entity_id,
            "power_tracking": getattr(req, "power_tracking", True),
            "availability_tracking": getattr(req, "availability_tracking", True),
            "usage_tracking": getattr(req, "usage_tracking", True),
            "offline_alert_threshold_minutes": getattr(req, "offline_alert_threshold_minutes", 30),
            "group_id": getattr(req, "group_id", None),
            "owner_user_id": user_context.user,
        }
        await _call_identity("POST", "/api/telemetry/enroll", payload)
        return ExecutionResult(
            status="SUCCESS",
            message=f"Enrolled '{req.entity_id}' in telemetry monitoring",
            service="telemetry",
        )
    except Exception as e:
        log.error(f"Telemetry enroll failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="telemetry")


async def handle_telemetry_unenroll(req) -> ExecutionResult:
    """Unenroll a device from telemetry monitoring."""
    try:
        await _call_identity("DELETE", f"/api/telemetry/enroll/{req.entity_id}")
        return ExecutionResult(
            status="SUCCESS",
            message=f"Unenrolled '{req.entity_id}' from telemetry monitoring",
            service="telemetry",
        )
    except Exception as e:
        log.error(f"Telemetry unenroll failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="telemetry")


async def handle_telemetry_list_enrolled() -> ExecutionResult:
    """List all enrolled devices."""
    try:
        enrollments = await _call_identity("GET", "/api/telemetry/enroll")
        return ExecutionResult(
            status="SUCCESS",
            message="Enrolled devices retrieved",
            service="telemetry",
            detail={"enrollments": enrollments},
        )
    except Exception as e:
        log.error(f"Telemetry list enrollments failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="telemetry")


# ─── Snapshot Ingestion ────────────────────────────────────────────────────────

async def handle_telemetry_snapshot(req) -> ExecutionResult:
    """Ingest a telemetry snapshot for an enrolled device."""
    try:
        payload = {
            "entity_id": req.entity_id,
            "power_w": getattr(req, "power_w", None),
            "is_available": getattr(req, "is_available", True),
            "state": getattr(req, "state", None),
            "source": getattr(req, "source", "poll"),
        }
        await _call_identity("POST", "/api/telemetry/snapshot", payload)
        return ExecutionResult(
            status="SUCCESS",
            message=f"Snapshot recorded for '{req.entity_id}'",
            service="telemetry",
        )
    except Exception as e:
        log.error(f"Telemetry snapshot failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="telemetry")


# ─── Query ─────────────────────────────────────────────────────────────────────

async def handle_telemetry_query(req) -> ExecutionResult:
    """Query telemetry data for an enrolled device."""
    try:
        params = {
            "hours": getattr(req, "hours", 24),
        }
        if getattr(req, "dimension", None):
            params["dimension"] = req.dimension
        data = await _call_identity("GET", f"/api/telemetry/data/{req.entity_id}")
        return ExecutionResult(
            status="SUCCESS",
            message=f"Telemetry data retrieved for '{req.entity_id}'",
            service="telemetry",
            detail=data,
        )
    except Exception as e:
        log.error(f"Telemetry query failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="telemetry")


async def handle_telemetry_summary(req) -> ExecutionResult:
    """Get a summary of telemetry data for an enrolled device."""
    try:
        data = await _call_identity("GET", f"/api/telemetry/summary/{req.entity_id}")
        return ExecutionResult(
            status="SUCCESS",
            message=f"Telemetry summary retrieved for '{req.entity_id}'",
            service="telemetry",
            detail=data,
        )
    except Exception as e:
        log.error(f"Telemetry summary failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="telemetry")


# ─── LLM Pattern Analysis ──────────────────────────────────────────────────────

async def handle_telemetry_analyze(req, user_context: UserContext) -> ExecutionResult:
    """Trigger LLM pattern analysis for enrolled devices."""
    try:
        payload = {
            "entity_id": getattr(req, "entity_id", None),
            "hours": getattr(req, "hours", 168),
            "force_analysis": getattr(req, "force_analysis", False),
        }
        data = await _call_identity("POST", "/api/telemetry/analyze", payload)
        return ExecutionResult(
            status="SUCCESS",
            message="Telemetry analysis completed",
            service="telemetry",
            detail=data,
        )
    except Exception as e:
        log.error(f"Telemetry analysis failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="telemetry")


async def handle_telemetry_insights() -> ExecutionResult:
    """Retrieve stored LLM insights."""
    try:
        data = await _call_identity("GET", "/api/telemetry/insights")
        return ExecutionResult(
            status="SUCCESS",
            message="Telemetry insights retrieved",
            service="telemetry",
            detail=data,
        )
    except Exception as e:
        log.error(f"Telemetry insights retrieval failed: {e}")
        return ExecutionResult(status="FAILURE", message=str(e), service="telemetry")
