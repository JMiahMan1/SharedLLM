# app/logic/media_ops.py
import re
import asyncio
import requests
from settings import log, run_blocking, HA_URL
from .utils import safe_similarity_search

# App Package IDs for Android TV Smart Routing
APP_PACKAGES = {
    "youtube": "com.google.android.youtube.tv",
    "netflix": "com.netflix.ninja",
    "disney": "com.disney.disneyplus",
    "disney+": "com.disney.disneyplus",
    "spotify": "com.spotify.tv.android",
    "prime video": "com.amazon.amazonvideo.livingroom",
    "amazon prime": "com.amazon.amazonvideo.livingroom",
    "plex": "com.plexapp.android",
    "twitch": "tv.twitch.android.app",
    "kodi": "org.xbmc.kodi",
    "hulu": "com.hulu.livingroomplus",
    "hbo": "com.hbo.hbonow",
    "max": "com.wbd.stream"
}

def _get_last_entity_key(user: str) -> str:
    return f"rag:last_entity:{user}"

def _set_last_entity(redis_client, user: str, entity_id: str):
    """Caches the last used entity ID for context awareness (24h TTL)."""
    if redis_client and entity_id:
        redis_client.setex(_get_last_entity_key(user), 86400, entity_id)

def get_last_entity(redis_client, user: str) -> str:
    """Retrieves the last used entity ID from cache."""
    if redis_client:
        val = redis_client.get(_get_last_entity_key(user))
        return val.decode('utf-8') if isinstance(val, bytes) else val
    return None

async def get_entity_state(entity_id: str, user_creds: dict) -> str:
    """Fetches the current state of an entity from Home Assistant."""
    if not HA_URL: return "unknown"
    
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

async def execute_ha_service(domain, service, entity_id, user_creds, service_data=None, redis_client=None):
    """
    Executes a Home Assistant Service Call (e.g., turn_on, play_media).
    Retries up to 3 times on failure.
    """
    if not HA_URL: return "Error: Home Assistant URL not configured."

    url = f"{HA_URL.rstrip('/')}/api/services/{domain}/{service}"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}
    payload = {"entity_id": entity_id, **(service_data or {})}
    
    log.info(f"EXEC HA: {domain}.{service} on {entity_id} | Data: {service_data}")
    
    last_err = None
    for i in range(3):
        try:
            def _post():
                return requests.post(url, json=payload, headers=headers, timeout=5.0)
            
            r = await run_blocking(_post)
            
            if r.status_code < 400:
                # Success! Cache this entity as the "Active Context"
                _set_last_entity(redis_client, user_creds.get("user"), entity_id)
                
                # Get friendly name for the response to make it natural
                friendly_name = entity_id
                try:
                     state_url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
                     def _get_name():
                         return requests.get(state_url, headers=headers, timeout=1.0)
                     
                     r_state = await run_blocking(_get_name)
                     if r_state.status_code == 200:
                         friendly_name = r_state.json().get("attributes", {}).get("friendly_name", entity_id)
                except: pass
                
                verb = service.replace("_", " ")
                return f"Sent command to {verb} the {friendly_name}."
            
            last_err = f"HTTP {r.status_code}: {r.text}"
            
        except Exception as e:
            last_err = str(e)
        
        # Wait briefly before retry
        await asyncio.sleep(0.5)
    
    log.error(f"Failed to execute HA command: {last_err}")
    return f"Failed: {last_err}"

async def smart_resolve_entity(query_name: str, intent: str, ha_collection) -> tuple:
    """
    Finds the BEST entity ID for a given name and intent.
    Returns (entity_id, integration_platform)
    """
    if not ha_collection: return (None, None)

    # 1. Find all candidates via Vector Search
    docs = await run_blocking(lambda: safe_similarity_search(ha_collection, query_name, k=5))
    if not docs: return (None, None)

    candidates = []
    for d in docs:
        eid = d.metadata.get("entity_id")
        integration = d.metadata.get("integration", "unknown")
        if eid:
            candidates.append((eid, integration))
    
    if not candidates: return (None, None)
    
    # 2. Determine Preference based on Intent
    preferred_type = "generic"
    
    if intent == "play_media":
        # If request contains an App Name -> Prefer Android TV (non-mass)
        is_app_request = any(app in query_name.lower() for app in APP_PACKAGES)
        if is_app_request:
             preferred_type = "android"
        else:
             # If generic Play -> Prefer Music Assistant for better queuing
             preferred_type = "mass"
             
    elif intent in ["turn_on", "turn_off", "toggle", "nav_up", "nav_down", "nav_enter", "nav_home", "nav_back"]:
        # Power/Nav -> Prefer Remote or Android TV
        preferred_type = "remote"

    log.info(f"Smart Resolving '{query_name}' for intent '{intent}'. Preference: {preferred_type}. Candidates: {candidates}")

    # 3. Select Best Match from Candidates
    best_match = candidates[0] # Default to first result if no specific preference met
    
    for eid, integration in candidates:
        # Music Assistant Preference
        if preferred_type == "mass":
            if "music_assistant" in integration or "mass" in integration:
                return (eid, integration) # Found specific match
        
        # Android/Remote Preference
        elif preferred_type == "remote" or preferred_type == "android":
            if "androidtv" in integration or "remote" in eid.split('.')[0]:
                return (eid, integration)
                
    return best_match

async def handle_media_command(intent: str, query: str, entity_id: str, user_creds: dict, ha_collection, redis_client):
    """
    Main entry point for routing media/power commands.
    """
    q_low = query.lower()
    
    # 1. Resolve Target Entity
    # If entity_id is passed (from direct match), use it. Otherwise resolve from query text.
    integration = "unknown"

    if not entity_id:
        target_name = q_low
        # Strip action phrases to isolate the device name
        for phrase in ["turn on", "turn off", "toggle", "play", "stop", "the", "please", " on ", "open", "launch"]:
            target_name = target_name.replace(phrase, " ")
        target_name = target_name.strip()

        if not target_name:
             # Try Context (Last used entity)
             entity_id = get_last_entity(redis_client, user_creds.get("user"))
             if not entity_id: return "Could not determine which device you mean. Please specify."
        else:
            # Use Smart Resolution
            entity_id, integration = await smart_resolve_entity(target_name, intent, ha_collection)
            if not entity_id: return f"I couldn't find a device named '{target_name}'."

    # --- EXECUTION ROUTING ---
    domain = entity_id.split('.')[0]
    service = intent
    service_data = {}

    # A. Power & Navigation (Remote / Switch / Light)
    if intent in ["turn_on", "turn_off", "toggle"] or intent.startswith("nav_"):
        
        # Handle Navigation (Remote specific)
        if intent.startswith("nav_"):
            cmd_map = {
                "nav_up": "DPAD_UP", "nav_down": "DPAD_DOWN", 
                "nav_left": "DPAD_LEFT", "nav_right": "DPAD_RIGHT",
                "nav_enter": "DPAD_CENTER", "nav_back": "BACK", 
                "nav_home": "HOME"
            }
            
            cmd = cmd_map.get(intent)
            if cmd:
                service = "send_command"
                domain = "remote"
                service_data = {"command": cmd}
                
                # If we resolved a media_player, try to guess the remote entity
                if "media_player" in entity_id:
                    # Heuristic: Replace domain and try
                    possible_remote = entity_id.replace("media_player", "remote")
                    entity_id = possible_remote
        
        # Handle Power (Generic)
        elif domain == "remote":
             # Map turn_on -> remote.turn_on
             service = "turn_" + intent.split("_")[1] 
        elif domain not in ["light", "switch", "media_player"]:
             # Fallback for other domains
             domain = "homeassistant"

        return await execute_ha_service(domain, service, entity_id, user_creds, service_data, redis_client)

    # B. App Launching / Media Playback
    if intent == "play_media" or intent == "open_app":
        domain = "media_player"
        
        # Check for App Launch Request
        target_pkg = None
        for app, pkg in APP_PACKAGES.items():
            if app in q_low:
                target_pkg = pkg
                break
        
        if target_pkg:
            # Android TV App Launch
            # Ensure we don't send App commands to Music Assistant entities
            if "music_assistant" in integration or "mass" in entity_id:
                 # Try to strip mass prefix to find the real player
                 entity_id = entity_id.replace("mass_", "").replace("_ma", "")
            
            service_data = {
                "media_content_id": target_pkg,
                "media_content_type": "app"
            }
            return await execute_ha_service(domain, "play_media", entity_id, user_creds, service_data, redis_client)

        # Generic Media / Music
        clean_title = q_low.replace("play", "").replace(" on ", "").strip()
        
        # Determine Content Type
        # If it's a TV/Chromecast, treat as Video unless specified
        ctype = "video" if any(x in entity_id.lower() for x in ["tv", "chromecast", "shield"]) else "music"
        
        # URL Detection
        if "http" in clean_title:
            ctype = "url"
            
        service_data = {
            "media_content_id": clean_title,
            "media_content_type": ctype,
            "enqueue": "play"
        }
        
        # Auto-Wake: If device is off, try to turn it on first
        state = await get_entity_state(entity_id, user_creds)
        if state in ["off", "unavailable"]:
             await execute_ha_service(domain, "turn_on", entity_id, user_creds, redis_client=redis_client)
             # Wait for boot (TVs are slow)
             await asyncio.sleep(4.0)
             
        return await execute_ha_service(domain, "play_media", entity_id, user_creds, service_data, redis_client)

    # C. Transport Controls
    if intent == "stop_media":
         # Maps to media_stop
         return await execute_ha_service("media_player", "media_stop", entity_id, user_creds, {}, redis_client)
         
    if intent == "media_next":
         return await execute_ha_service("media_player", "media_next_track", entity_id, user_creds, {}, redis_client)

    if intent == "media_previous":
         return await execute_ha_service("media_player", "media_previous_track", entity_id, user_creds, {}, redis_client)

    return None
