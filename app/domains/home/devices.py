# app/domains/home/devices.py
"""
General Home Assistant device utilities.
"""

import logging
import requests
import asyncio
from typing import Optional
from app.settings import run_blocking, HA_URL

log = logging.getLogger(__name__)


async def get_entity_state(entity_id: str, user_creds: dict) -> str:
    """
    Get the current state of a Home Assistant entity.

    Args:
        entity_id: The entity ID to check
        user_creds: User credentials with HA token

    Returns:
        Current state string or "unknown" if unable to determine
    """
    if not HA_URL:
        return "unknown"

    url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}

    try:
        def _fetch():
            return requests.get(url, headers=headers, timeout=2.0)

        r = await run_blocking(_fetch)
        if r.status_code == 200:
            return r.json().get("state", "unknown")
    except Exception as e:
        log.error(f"State fetch error for {entity_id}: {e}")

    return "unknown"
