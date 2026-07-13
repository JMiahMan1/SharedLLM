# services/shared/rag_client.py
"""Best-effort producers that push structured data into the RAG service.

These are fire-and-forget helpers used by other services (gateway worker,
execution intercom/telemetry, dns_sync) to populate the Section 6 relational
collections (mission_history, conversation_memory, network_topology,
telemetry_alerts). Every call is wrapped so a RAG outage never breaks the
primary flow that triggered it.
"""
from __future__ import annotations

import logging

import aiohttp

from services.config import INTERNAL_SECRET, RAG_SVC_URL

log = logging.getLogger("rag_client")


async def _post(path: str, payload: dict, timeout: float = 10.0) -> bool:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as client, client.post(
            f"{RAG_SVC_URL}{path}",
            json=payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        ) as resp:
            return resp.status == 200
    except Exception as e:  # pragma: no cover - network dependent
        log.warning(f"[rag_client] push to {path} failed: {e}")
        return False


async def push_mission(
    mission_id: str | int,
    task_description: str,
    final_status: str,
    error_summary: str = "",
    steps: list[dict] | None = None,
    user_id: str = "default",
) -> bool:
    return await _post(
        "/rag/sync/missions",
        {
            "missions": [
                {
                    "mission_id": str(mission_id),
                    "task_description": task_description,
                    "final_status": final_status,
                    "error_summary": error_summary,
                    "steps": steps or [],
                    "user_id": user_id,
                }
            ]
        },
    )


async def push_conversation(
    speaker: str,
    text_content: str,
    room_id: str = "unknown",
    user_id: str = "default",
    timestamp: int | None = None,
) -> bool:
    return await _post(
        "/rag/sync/conversations",
        {
            "utterances": [
                {
                    "speaker": speaker,
                    "text_content": text_content,
                    "room_id": room_id,
                    "user_id": user_id,
                    "timestamp": timestamp or int(__import__("time").time()),
                }
            ]
        },
    )


async def push_network_topology(containers: list[dict], user_id: str = "default") -> bool:
    return await _post(
        "/rag/sync/network",
        {"containers": containers, "user_id": user_id},
    )


async def push_telemetry_alert(
    entity_id: str,
    alert_type: str,
    severity: str,
    content: str,
    user_id: str = "default",
) -> bool:
    return await _post(
        "/rag/sync/telemetry_alerts",
        {
            "alerts": [
                {
                    "entity_id": entity_id,
                    "alert_type": alert_type,
                    "severity": severity,
                    "content": content,
                    "user_id": user_id,
                }
            ]
        },
    )
