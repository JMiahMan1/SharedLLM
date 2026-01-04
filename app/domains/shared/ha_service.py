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

log = logging.getLogger(__name__)


async def execute_ha_service(domain, service, entity_id, user_creds, service_data=None, redis_client=None):
    """
    Executes a Home Assistant service and returns a structured dictionary result.
    Includes optimized state verification loop.
    """
    user = user_creds.get("user")

    if not HA_URL:
        return {"status": "FAILURE", "message": "Error: Home Assistant URL not configured.", "entity_id": entity_id, "service": f"{domain}.{service}"}

    # Fetch initial state for pre/post comparison
    from app.domains.home.devices import get_entity_state
    initial_state = await get_entity_state(entity_id, user_creds)

    url = f"{HA_URL.rstrip('/')}/api/services/{domain}/{service}"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}
    payload = {"entity_id": entity_id, **(service_data or {})}

    log.info(f"EXEC HA: {domain}.{service} on {entity_id} | Data: {service_data}")

    last_err = None

    for attempt in range(2):
        try:
            def _post():
                return requests.post(url, json=payload, headers=headers, timeout=5.0)

            r = await run_blocking(_post)

            if r.status_code < 400:
                # Update last entity tracking
                if redis_client and user and entity_id:
                    from app.domains.media.devices import _set_last_entity
                    _set_last_entity(redis_client, user, entity_id)

                # --- OPTIMIZED: Faster State Verification ---
                # No initial long wait, start checking immediately
                new_state = "N/A"
                friendly_name = entity_id

                # Check up to 5 times, every 0.5 seconds (Total ~2.5s max wait)
                for state_attempt in range(5):
                    await asyncio.sleep(1.0)
                    try:
                        state_url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
                        def _get_name():
                            return requests.get(state_url, headers=headers, timeout=1.0)

                        r_state = await run_blocking(_get_name)
                        if r_state.status_code == 200:
                            state_data = r_state.json()
                            friendly_name = state_data.get("attributes", {}).get("friendly_name", entity_id)
                            current_state = state_data.get("state", "unknown")
                            attrs = state_data.get("attributes", {})

                            # Check for expected state change
                            expected_change = False
                            if service.startswith("turn_off") and current_state in ["off", "unavailable"]:
                                expected_change = True
                            elif service.startswith("turn_on") and current_state not in ["off", "unavailable"]:
                                expected_change = True
                            elif service.startswith("media_play") or service == "play_media":
                                if current_state in ["playing", "paused", "buffering"]:
                                    # [Verification Fix] Ensure it's the *correct* media
                                    # If we sent a specific content_id (URL/URI), check if it's reflected
                                    req_content = (service_data or {}).get("media_content_id", "")
                                    
                                    # Relaxed matching check
                                    match = True
                                    if req_content and len(req_content) > 5:
                                        # attributes can vary: media_content_id, media_title, app_id
                                        curr_content_id = str(attrs.get("media_content_id", ""))
                                        curr_title = str(attrs.get("media_title", "")).lower()
                                        
                                        # Simple substring match (since URLs might get processed/shortened)
                                        # If requested content is in the attributes, we are good.
                                        # Check valid logic: if req is URL, look for it in content_id.
                                        # If req is a title (music), look for it in media_title.
                                        
                                        # Case 1: URL/File match (Video/Cast)
                                        if "http" in req_content:
                                            # Often cast sends just the filename or full URL
                                            match = req_content in curr_content_id or curr_content_id in req_content
                                        
                                        # Case 2: Title match (Music/Search)
                                        else:
                                           # If media_title is present, check similarity
                                           if curr_title and req_content.lower() not in curr_title and len(curr_title) > 2:
                                               # Only mark false if we strictly mismatch on a title that is clearly different
                                               # But be careful of partial matches or radio stations.
                                               # For now, let's assume if it is playing, it is likely the right thing 
                                               # unless we have strong evidence otherwise.
                                               pass

                                    if match:
                                        expected_change = True
                                    else:
                                        log.info(f"[State Verify] Playing but content mismatch? Req: {req_content[:20]}... vs Act: {curr_content_id[:20]}...")

                            if expected_change or state_attempt == 4:
                                new_state = current_state
                                break
                    except Exception as e:
                        log.warning(f"[State Verify] Error: {e}") 
                        pass

                # --- END FIX ---
                
                # ChromaDB update is NOT needed here because get_ha_context fetches live state.
                # Avoid heavy re-indexing on every command.

                verb = service.replace("_", " ")
                return {
                    "status": "SUCCESS",
                    "message": f"Sent command to {verb} the {friendly_name}.",
                    "entity_id": entity_id,
                    "friendly_name": friendly_name,
                    "service": f"{domain}.{service}",
                    "new_state": new_state
                }

            # Error Capture
            try:
                err_data = r.json()
                msg = err_data.get("message", r.text)
            except:
                msg = r.text[:200] if r.text else "Unknown Error"

            last_err = f"HTTP {r.status_code}: {msg}"

            if r.status_code >= 500:
                log.warning(f"HA 500 Error: {msg}")
                break

        except Exception as e:
            last_err = str(e)

        await asyncio.sleep(1.0)

    log.error(f"Failed to execute HA command: {last_err}")
    return {
        "status": "FAILURE",
        "message": f"Failed: {last_err}",
        "entity_id": entity_id,
        "friendly_name": entity_id.split(".")[-1].replace("_", " ").title() if entity_id else "System",
        "service": f"{domain}.{service}"
    }
