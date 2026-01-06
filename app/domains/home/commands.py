# app/domains/home/commands.py
"""
General Home Assistant command handling.
"""

import logging
from typing import List, Dict, Optional

log = logging.getLogger(__name__)


async def handle_home_command(intent: str, query: str, entity_id: str, user_creds: dict, redis_client=None) -> List[Dict]:
    """
    Handle general Home Assistant commands that don't fit into specific domains.

    Args:
        intent: The intent (turn_on, turn_off, toggle, etc.)
        query: The original query text
        entity_id: The entity ID
        user_creds: User credentials for HA API
        redis_client: Optional Redis client for caching

    Returns:
        List of result dictionaries
    """
    from app.domains.shared import execute_ha_service

    domain = entity_id.split('.')[0] if entity_id else "homeassistant"
    service = intent

    # Map intents to services
    if intent in ["turn_on", "turn_off", "toggle"]:
        # These are already correct service names
        pass
    elif intent.startswith("nav_"):
        # Navigation commands - convert to remote commands
        domain = "remote"
        nav_map = {
            "nav_up": "DPAD_UP",
            "nav_down": "DPAD_DOWN",
            "nav_left": "DPAD_LEFT",
            "nav_right": "DPAD_RIGHT",
            "nav_enter": "DPAD_CENTER",
            "nav_back": "BACK",
            "nav_home": "HOME"
        }
        if intent in nav_map:
            service = "send_command"
            service_data = {"command": nav_map[intent]}
        else:
            return [{"status": "FAILURE", "message": f"Unknown navigation command: {intent}", "entity_id": entity_id, "service": intent}]
    else:
        # Unknown intent
        return [{"status": "FAILURE", "message": f"Unsupported home command: {intent}", "entity_id": entity_id, "service": intent}]

    # Execute the command
    service_data = locals().get('service_data', {})  # Get service_data if it was set above
    return [await execute_ha_service(domain, service, entity_id, user_creds, service_data, redis_client)]
