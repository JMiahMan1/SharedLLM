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

# --- CENTRALIZED INTENT DEFINITIONS ---
# Used by pipeline.py for routing
MEDIA_INTENTS = [
    "turn_on", "turn_off", "toggle", 
    "stop_media", "play_media", "open_app",
    "media_next", "media_previous",
    "nav_up", "nav_down", "nav_left", "nav_right", 
    "nav_enter", "nav_back", "nav_home"
]

# Used by pipeline.py for Regex Overrides
REGEX_INTENT_MAP = {
    r"\b(open|launch|start)\s+(netflix|youtube|disney|hulu|plex|prime|spotify)": "open_app",
    r"\b(play)\b": "play_media",
    r"\b(stop|pause)\b": "stop_media",
    r"\b(skip|next)\b": "media_next",
    r"\b(previous|back|prev)\b": "media_previous",
    r"\b(scroll|move|go)\s+up\b": "nav_up",
    r"\b(scroll|move|go)\s+down\b": "nav_down",
    r"\b(scroll|move|go)\s+left\b": "nav_left",
    r"\b(scroll|move|go)\s+right\b": "nav_right",
    r"\bgo back\b|\bback\b": "nav_back",
    r"\bgo home\b|\bhome\b": "nav_home",
    r"\bselect\b|\benter\b|\bok\b": "nav_enter",
}
# --------------------------------------


def _get_last_entity_key(user: str) -> str:
    return f"rag:last_entity:{user}"

def _set_last_entity(redis_client, user: str, entity_id: str):
    if redis_client and entity_id:
        redis_client.setex(_get_last_entity_key(user), 86400, entity_id)

def get_last_entity(redis_client, user: str) -> str:
    if redis_client:
        val = redis_client.get(_get_last_entity_key(user))
        return val.decode('utf-8') if isinstance(val, bytes) else val
    return None


# ------------------------------------
# STATE FETCH
# ------------------------------------
async def get_entity_state(entity_id: str, user_creds: dict) -> str:
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

async def get_active_media_players(user_creds: dict) -> list:
    """Returns a list of entity_ids for media players that are currently playing or paused."""
    if not HA_URL: return []
    
    url = f"{HA_URL.rstrip('/')}/api/states"
    headers = {"Authorization": f"Bearer {user_creds['ha_token']}"}
    
    try:
        def _fetch_all():
            return requests.get(url, headers=headers, timeout=3.0)
        
        r = await run_blocking(_fetch_all)
        if r.status_code == 200:
            all_states = r.json()
            active = []
            for s in all_states:
                eid = s.get("entity_id", "")
                if eid.startswith("media_player."):
                    state = s.get("state", "off")
                    if state in ["playing", "paused", "buffering"]:
                        active.append(eid)
            return active
    except Exception as e:
        log.error(f"Error fetching active players: {e}")
        return []
    return []


# ------------------------------------
# SERVICE EXECUTION
# ------------------------------------
async def execute_ha_service(domain, service, entity_id, user_creds, service_data=None, redis_client=None):
    if not HA_URL:
        return "Error: Home Assistant URL not configured."

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
                _set_last_entity(redis_client, user_creds.get("user"), entity_id)

                # Friendly Name Fetch
                friendly_name = entity_id
                try:
                    state_url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
                    def _get_name():
                        return requests.get(state_url, headers=headers, timeout=1.0)

                    r_state = await run_blocking(_get_name)
                    if r_state.status_code == 200:
                        friendly_name = r_state.json().get("attributes", {}).get("friendly_name", entity_id)
                except:
                    pass

                verb = service.replace("_", " ")
                return f"Sent command to {verb} the {friendly_name}."
            
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

        await asyncio.sleep(0.5)

    log.error(f"Failed to execute HA command: {last_err}")
    return f"Failed: {last_err}"


# ------------------------------------
# SMART ENTITY RESOLUTION
# ------------------------------------
async def smart_resolve_entity(query_name: str, intent: str, ha_collection, is_music: bool = False) -> tuple:
    if not ha_collection or not query_name.strip():
        return (None, None)

    docs = await run_blocking(lambda: safe_similarity_search(ha_collection, query_name, k=15))
    if not docs:
        return (None, None)

    candidates = []
    for d in docs:
        eid = d.metadata.get("entity_id")
        integration = d.metadata.get("integration", "unknown")
        if eid:
            domain = eid.split('.')[0]
            
            # Domain Filtering
            if intent in ["play_media", "open_app", "media_next", "media_previous", "stop_media"]:
                if domain not in ["media_player", "group", "script"]:
                    continue

            if intent in ["turn_on", "turn_off", "toggle"]:
                 if domain in ["sensor", "binary_sensor", "sun", "weather"]:
                     continue

            candidates.append((eid, integration))

    if not candidates:
        return (None, None)

    q_low = query_name.lower()
    
    # --- RESTRUCTURED LOGIC: STRICT FILTERING FIRST (CRITICAL FIX) ---
    if is_music:
        # STRICT MUSIC FILTER: If is_music is True, we only accept music_assistant entities.
        for eid, integration in candidates:
            if "music_assistant" in integration:
                # Return the highest-ranking MA player immediately if found
                return eid, integration
        
        # FIX: If it's a 'play' intent and the device is a TV/Generic Media Player, allow it to fallback 
        # to a generic media_player if no MA entity was found.
        if intent == "play_media":
             for eid, integration in candidates:
                 if eid.startswith("media_player.") and any(x in eid.lower() for x in ["tv", "chromecast", "shield", "androidtv"]):
                      log.info(f"Strict Music Fallback: Allowing generic TV media player: {eid}")
                      return eid, integration
                      
        # If we reached here, no music_assistant entity was found in the top 15 results.
        log.warning(f"Strict Music Mode: No Music Assistant entity found for '{query_name}'. Returning None.")
        return (None, None)
    
    # --- NON-STRICT / GENERIC LOGIC ---
    
    preferred_type = "generic"
    
    # Determine Preference for non-music intents
    if intent == "play_media" and any(app in q_low for app in APP_PACKAGES):
        preferred_type = "android"
    elif intent in ["open_app"]:
        preferred_type = "android"
    elif intent in ["turn_on", "turn_off", "toggle"] or intent.startswith("nav_"):
        preferred_type = "remote"

    log.info(f"Smart Resolving '{query_name}' Intent '{intent}' Pref '{preferred_type}' Candidates {candidates[:3]}...")

    # Standard Preference Logic for generic/remote
    for eid, integration in candidates:
        if preferred_type == "android" and ("media_player" in eid):
            return eid, integration
        if preferred_type == "remote" and ("remote" in eid or "androidtv" in integration):
            return eid, integration

    # Default fallback for generic intents
    return candidates[0]


# ------------------------------------
# MEDIA COMMAND ROUTING
# ------------------------------------
async def handle_media_command(intent: str, query: str, entity_id: str, user_creds: dict, ha_collection, redis_client):
    q_low = query.lower()
    integration = "unknown"

    # 1. EARLY MUSIC DETECTION
    music_keywords = ["music", "song", "artist", "album", "track", "playlist", "radio"]
    # If play_media intent is active AND we have music keywords, strict resolution applies.
    is_music_request = any(x in q_low for x in music_keywords)

    strict_resolution = is_music_request and intent == "play_media"
    is_transport = intent in ["media_next", "media_previous", "stop_media"]

    # --- TRANSPORT SHORT CIRCUIT (High Confidence/Explicit Target) ---
    if is_transport:
        device_match = re.search(r"\b(on|in)\s+(the\s+)?(office|tv|bedroom|kitchen|speaker|remote|media)\b", q_low)
        
        # 2a. Resolve device name from query if present
        if not entity_id and device_match:
             potential_device_name = q_low.split(device_match.group(1))[-1].strip()
             if potential_device_name:
                 # IMPORTANT: is_music=False here ensures we don't force Mass for skip/stop commands
                 resolved_id, resolved_int = await smart_resolve_entity(potential_device_name, intent, ha_collection, is_music=False)
                 if resolved_id:
                    log.info(f"Transport Short Circuit: Found explicit device {resolved_id} from query.")
                    entity_id = resolved_id
                    integration = resolved_int
        
        # 2b. If we have an entity_id now (from Redis or short circuit), check its state
        if entity_id:
             state = await get_entity_state(entity_id, user_creds)
             if state in ["playing", "paused", "buffering"]:
                 log.info(f"Transport Short Circuit: Device {entity_id} is active, proceeding directly.")
                 domain = entity_id.split('.')[0]
                 return await _execute_transport_command(intent, entity_id, domain, user_creds, integration, redis_client)

    # ------------------------------------------------------------------
    # 2. FULL RESOLUTION PATH (Fallback for Ambiguous/Play/Unresolved Transport)
    # ------------------------------------------------------------------
    clean_title = q_low
    
    # "On" Splitting
    if not entity_id and " on " in clean_title:
        parts = clean_title.rpartition(" on ")
        potential_content = parts[0].strip()
        potential_device = parts[2].strip()
        
        if len(potential_device) > 2:
            # We rely on smart_resolve_entity to return the correct MA entity or None here.
            resolved_id, resolved_int = await smart_resolve_entity(potential_device, intent, ha_collection, is_music=strict_resolution)
                        
            if resolved_id:
                # INTEGRITY CHECK: If strict_resolution was true, we enforce that the returned entity is MA.
                if strict_resolution and "music_assistant" not in resolved_int and not any(x in resolved_id.lower() for x in ["tv", "chromecast", "shield", "androidtv"]):
                    # This means the resolution failed to honor strict mode (MA entity not found in top candidates).
                    log.error(f"Strict Resolution failure: Resolved {resolved_id} ({resolved_int}) which is not MA/TV.")
                    return f"I couldn't find a Music Assistant device named '{potential_device}'."
                    
                entity_id = resolved_id
                integration = resolved_int
                clean_title = potential_content
                log.info(f"'On' Split Success: Device='{potential_device}' ({entity_id}), Content='{clean_title}'")
            else:
                 # If resolved_id is None, it means strict mode failed to find a target.
                 return f"I couldn't find a device named '{potential_device}' to play media."
    
    # Standard Resolution
    if not entity_id:
        cleaned_for_res = clean_title
        for p in ["turn on", "turn off", "toggle", "play", "stop", "open", "launch", "the", " on ", " please "]:
            cleaned_for_res = cleaned_for_res.replace(p, " ")
        cleaned_for_res = cleaned_for_res.strip()

        if not cleaned_for_res:
            entity_id = get_last_entity(redis_client, user_creds.get("user"))
        else:
            entity_id, integration = await smart_resolve_entity(cleaned_for_res, intent, ha_collection, is_music=strict_resolution)

    # --- POST-RESOLUTION DEFINITION ---
    if entity_id:
        domain = entity_id.split('.')[0]
    
    if not entity_id and intent not in ["turn_on", "turn_off", "toggle"]: # Safety check
         return "Could not determine which device you mean."

    # 3. TRANSPORT COMMAND REDIRECTION (For Unresolved/Idle Transport)
    if is_transport:
        should_scan = False
        if not entity_id:
            should_scan = True
        else:
            state = await get_entity_state(entity_id, user_creds)
            if state not in ["playing", "paused", "buffering"]:
                log.info(f"Targeted entity {entity_id} is {state}. Scanning for active players...")
                should_scan = True

        if should_scan:
            active_players = await get_active_media_players(user_creds)
            if active_players:
                if entity_id and entity_id in active_players:
                    pass
                else:
                    new_entity = active_players[0]
                    log.info(f"Redirecting {intent} from {entity_id or 'None'} to active device: {new_entity}")
                    entity_id = new_entity
            else:
                if not entity_id:
                     return "No active media players found to control."
                
        domain = entity_id.split('.')[0]
        return await _execute_transport_command(intent, entity_id, domain, user_creds, integration, redis_client)


    if not entity_id and intent not in ["turn_on", "turn_off", "toggle"]: # Safety check
         return "Could not determine which device you mean."

    domain = entity_id.split('.')[0]
    service = intent
    service_data = {}

    # -------------------------------------------------
    # POWER, NAVIGATION
    # -------------------------------------------------
    if intent in ["turn_on", "turn_off", "toggle"] or intent.startswith("nav_"):
        if intent.startswith("nav_"):
            cmd_map = {
                "nav_up": "DPAD_UP", "nav_down": "DPAD_DOWN",
                "nav_left": "DPAD_LEFT", "nav_right": "DPAD_RIGHT",
                "nav_enter": "DPAD_CENTER", "nav_back": "BACK",
                "nav_home": "HOME",
            }
            service = "send_command"
            domain = "remote"
            service_data = {"command": cmd_map.get(intent)}
            if "media_player" in entity_id:
                entity_id = entity_id.replace("media_player", "remote")
        elif domain == "remote":
            service = "turn_" + intent.split("_")[1]
        if domain not in ["light", "switch", "remote", "media_player"]:
            domain = "homeassistant"
        return await execute_ha_service(domain, service, entity_id, user_creds, service_data, redis_client)

    # -------------------------------------------------
    # MEDIA (PLAY / OPEN APP)
    # -------------------------------------------------
    if intent in ["play_media", "open_app"]:
       
        # APP LAUNCH
        for app, pkg in APP_PACKAGES.items():
            if app in q_low:
                return await execute_ha_service(
                    "media_player", "play_media", entity_id, user_creds,
                    {"media_content_id": pkg, "media_content_type": "app"},
                    redis_client
                )

        # CONTENT CLEANING
        clean_title = re.sub(r"\b(play|please|music|song|from|on|open|launch|playback|listen to)\b", " ", clean_title)
        clean_title = re.sub(r"\b(album|track|playlist|artist)\b", " ", clean_title)
        clean_title = re.sub(r"\b(by)\b", " ", clean_title)
        clean_title = re.sub(r"\bthe\b", " ", clean_title)

        raw_dev = entity_id.split(".")[-1] if entity_id else ""
        dev_tokens = [t for t in raw_dev.split("_") if t]
        variants = set()
        for i in range(len(dev_tokens)):
            for j in range(i + 1, len(dev_tokens) + 1):
                variants.add(" ".join(dev_tokens[i:j]))
        if raw_dev: variants.add(raw_dev.replace("_", " "))

        for v in sorted(variants, key=lambda s: -len(s)):
            if not v: continue
            clean_title = re.sub(r"\b" + re.escape(v) + r"\b", " ", clean_title)

        clean_title = re.sub(r"[^\w\s]", " ", clean_title) 
        clean_title = re.sub(r"\s+", " ", clean_title).strip()

        # CONTENT TYPE
        ctype = "music"
        is_tv = any(x in entity_id.lower() for x in ["tv", "chromecast", "shield", "androidtv"])

        if is_music_request and not is_tv:
            ctype = "music"
        elif is_tv:
            ctype = "video"
            
        if "music_assistant" in integration:
            ctype = "music"
        if "http" in clean_title:
            ctype = "url"
        
        if not clean_title:
             return "I understood the device, but not what to play. Please specify content."

        service_data = {
            "media_content_id": clean_title,
            "media_content_type": ctype,
            "enqueue": "play"
        }

        state = await get_entity_state(entity_id, user_creds)
        if state in ["off", "unavailable"]:
            await execute_ha_service(domain, "turn_on", entity_id, user_creds, redis_client=redis_client)
            await asyncio.sleep(4.0)

        result = await execute_ha_service(domain, "play_media", entity_id, user_creds, service_data, redis_client)
        
        # Self-Healing
        if "Failed" in result and "500" in result:
            new_type = "video" if ctype == "music" else "music"
            log.info(f"Self-Healing: Retrying '{clean_title}' as '{new_type}' on {entity_id}")
            service_data["media_content_type"] = new_type
            result = await execute_ha_service(domain, "play_media", entity_id, user_creds, service_data, redis_client)

        return result

    # -------------------------------------------------
    # FALLBACK FOR UNRESOLVED COMMANDS
    # -------------------------------------------------
    return None

async def _execute_transport_command(intent: str, entity_id: str, domain: str, user_creds: dict, integration: str, redis_client):
    """Executes media transport command with self-healing fallback prioritizing remote control."""
    
    if intent == "stop_media":
        result = await execute_ha_service("media_player", "media_stop", entity_id, user_creds, {}, redis_client)
        return result
        
    # --- CONSISTENCY FIX: Prioritize Remote if not Music Assistant or if Integration is Unknown ---
    is_mass = "music_assistant" in integration
    
    if intent == "media_next":
        remote_id = entity_id.replace("media_player", "remote")
        
        if is_mass:
            # 1. Try standard media service for known Music Assistant entities
            result = await execute_ha_service("media_player", "media_next_track", entity_id, user_creds, {}, redis_client)
        else:
            # 1. For generic/unknown/TV players, go straight to remote control for consistency
            log.info(f"Non-Mass/Unknown Integration ({integration}) for {entity_id}. Prioritizing remote command: DPAD_RIGHT on {remote_id}")
            result = await execute_ha_service("remote", "send_command", remote_id, user_creds, {"command": "DPAD_RIGHT"}, redis_client)

        # 2. If Mass failed, attempt remote as a final resort
        if "Failed" in result and is_mass:
            log.info(f"Mass media_next_track failed. Final fallback to remote: DPAD_RIGHT on {remote_id}")
            result = await execute_ha_service("remote", "send_command", remote_id, user_creds, {"command": "DPAD_RIGHT"}, redis_client)
        
        # 3. If remote failed on a non-Mass entity, no further easy fallback
        
        return result
            
    elif intent == "media_previous":
        remote_id = entity_id.replace("media_player", "remote")
        
        if is_mass:
            # 1. Try standard media service for known Music Assistant entities
            result = await execute_ha_service("media_player", "media_previous_track", entity_id, user_creds, {}, redis_client)
        else:
            # 1. For generic/unknown/TV players, go straight to remote control for consistency
            log.info(f"Non-Mass/Unknown Integration ({integration}) for {entity_id}. Prioritizing remote command: DPAD_LEFT on {remote_id}")
            result = await execute_ha_service("remote", "send_command", remote_id, user_creds, {"command": "DPAD_LEFT"}, redis_client)

        # 2. If Mass failed, attempt remote as a final resort
        if "Failed" in result and is_mass:
            log.info(f"Mass media_previous_track failed. Final fallback to remote: DPAD_LEFT on {remote_id}")
            result = await execute_ha_service("remote", "send_command", remote_id, user_creds, {"command": "DPAD_LEFT"}, redis_client)
            
        # 3. If remote failed on a non-Mass entity, no further easy fallback

        return result

    return None
