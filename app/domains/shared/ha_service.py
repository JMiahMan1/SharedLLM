# app/domains/shared/ha_service.py
"""
Shared Home Assistant service execution utilities.
"""

import json
import logging
import requests
import asyncio
from typing import Dict, Optional
from app.settings import run_blocking, HA_URL
from aiobreaker import CircuitBreaker

from datetime import timedelta
log = logging.getLogger(__name__)

ha_circuit_breaker = CircuitBreaker(fail_max=3, timeout_duration=timedelta(seconds=30))

@ha_circuit_breaker
async def execute_ha_service(domain, service, entity_id, user_creds, service_data=None, redis_client=None):
    """
    Executes a Home Assistant service and returns a structured dictionary result.
    Optimized to use aiohttp and circuit breakers with Redis-cached state lookups.
    """
    from app.main import http_session
    user = user_creds.get("user")

    if not HA_URL:
        return {"status": "FAILURE", "message": "Error: Home Assistant URL not configured.", "entity_id": entity_id, "service": f"{domain}.{service}"}

    url = f"{HA_URL.rstrip('/')}/api/services/{domain}/{service}"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}
    payload = {"entity_id": entity_id, **(service_data or {})}

    log.info(f"EXEC HA: {domain}.{service} on {entity_id} | Data: {service_data}")

    try:
        async with http_session.post(url, json=payload, headers=headers, timeout=5.0) as response:
            if response.status >= 400:
                err_text = await response.text()
                log.warning(f"HA Error: {err_text}")
                return {"status": "FAILURE", "message": err_text, "entity_id": entity_id, "service": f"{domain}.{service}"}
            
            # Update last entity tracking
            if redis_client and user and entity_id:
                from app.domains.media.devices import _set_last_entity
                _set_last_entity(redis_client, user, entity_id)
            
            # Use Redis for instant state lookup (populated by WS listener)
            new_state = "N/A"
            friendly_name = entity_id
            if redis_client:
                # Try to get state and friendly name from Redis cache if available
                cached_state = redis_client.hget(f"ha:state:{entity_id}", "state")
                cached_name = redis_client.hget(f"ha:state:{entity_id}", "friendly_name")
                if cached_state:
                    new_state = cached_state.decode("utf-8")
                if cached_name:
                    friendly_name = cached_name.decode("utf-8")
                    
            verb = service.replace("_", " ")
            return {
                "status": "SUCCESS",
                "message": f"Sent command to {verb} the {friendly_name}.",
                "entity_id": entity_id,
                "friendly_name": friendly_name,
                "service": f"{domain}.{service}",
                "new_state": new_state
            }
    except Exception as e:
        log.error(f"Failed to execute HA command: {e}")
        # The circuit breaker will handle repeated failures
        raise
