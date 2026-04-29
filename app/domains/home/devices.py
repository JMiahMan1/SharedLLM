# app/domains/home/devices.py
"""
General Home Assistant device utilities.
"""

import logging
import asyncio
from typing import Optional
from app.settings import HA_URL

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
    from app.settings import GlobalResources
    from app.main import http_session
    
    # 1. Check Redis Cache First (<1ms latency)
    if GlobalResources.redis_client:
        cached_state = GlobalResources.redis_client.hget(f"ha:state:{entity_id}", "state")
        if cached_state:
            return cached_state.decode("utf-8")
            
    if not HA_URL:
        return "unknown"

    url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}

    for attempt in range(3):
        try:
            async with http_session.get(url, headers=headers, timeout=5.0) as r:
                if r.status == 200:
                    data = await r.json()
                    return data.get("state", "unknown")
            break # Not a connection error, just a non-200, so break and return unknown
        except Exception as e:
            log.warning(f"Connection error fetching state for {entity_id} (attempt {attempt+1}): {e}")
            if attempt == 2: break
            await asyncio.sleep(1.0)

    return "unknown"
