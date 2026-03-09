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

    for attempt in range(3):
        try:
            def _fetch():
                return requests.get(url, headers=headers, timeout=5.0)

            r = await run_blocking(_fetch)
            if r.status_code == 200:
                return r.json().get("state", "unknown")
            break # Not a connection error, just a non-200, so break and return unknown
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as ce:
            log.warning(f"Connection error fetching state for {entity_id} (attempt {attempt+1}): {ce}")
            if attempt == 2: break
            await asyncio.sleep(1.0)
        except Exception as e:
            log.error(f"State fetch error for {entity_id}: {e}")
            break

    return "unknown"
