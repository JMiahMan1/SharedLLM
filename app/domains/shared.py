"""Shared domain utilities for the SharedLLM monolith.

Provides execute_ha_service() as a drop-in for scripts that need to
call Home Assistant services directly.
"""
from __future__ import annotations

import os
import logging
from typing import Any, Dict, Optional

log = logging.getLogger("app.domains.shared")

HA_URL = os.environ.get("HA_URL", "http://localhost:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")


async def execute_ha_service(
    domain: str,
    service: str,
    entity_id: Optional[str] = None,
    user_creds: Optional[Dict[str, str]] = None,
    service_data: Optional[Dict[str, Any]] = None,
    target: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute a Home Assistant service call.

    Uses the HA_URL and HA_TOKEN from environment or from user_creds if provided.
    Returns a dict with 'status' and optionally 'data'.
    """
    import httpx

    ha_url = HA_URL
    ha_token = HA_TOKEN

    if user_creds and isinstance(user_creds, dict):
        ha_url = user_creds.get("ha_url") or ha_url
        ha_token = user_creds.get("ha_token") or ha_token

    url = f"{ha_url.rstrip('/')}/api/services/{domain}/{service}"
    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}

    payload = {}
    if entity_id:
        payload["entity_id"] = entity_id
    if service_data:
        payload.update(service_data)
    if target:
        payload["target"] = target

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code in (200, 201):
                return {"status": "SUCCESS"}
            else:
                log.error(f"HA service call failed: {resp.status_code} {resp.text}")
                return {"status": "FAILURE", "detail": resp.text}
        except Exception as e:
            log.error(f"HA service call error: {e}")
            return {"status": "FAILURE", "detail": str(e)}
