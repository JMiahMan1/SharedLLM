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

    for _ in range(3):
        try:
            def _post():
                return requests.post(url, json=payload, headers=headers, timeout=5.0)

            r = await run_blocking(_post)

            if r.status_code < 400:
                _set_last_entity(redis_client, user_creds.get("user"), entity_id)

                # Friendly Name
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

            last_err = f"HTTP {r.status_code}: {r.text}"

        except Exception as e:
            last_err = str(e)

        await asyncio.sleep(0.5)

    log.error(f"Failed to execute HA command: {last_err}")
    return f"Failed: {last_err}"


# ------------------------------------
# SMART ENTITY RESOLUTION
# ------------------------------------
async def smart_resolve_entity(query_name: str, intent: str, ha_collection) -> tuple:
    if not ha_collection:
        return (None, None)

    docs = await run_blocking(lambda: safe_similarity_search(ha_collection, query_name, k=6))
    if not docs:
        return (None, None)

    candidates = []
    for d in docs:
        eid = d.metadata.get("entity_id")
        integration = d.metadata.get("integration", "unknown")
        if eid:
            candidates.append((eid, integration))

    if not candidates:
        return (None, None)

    q_low = query_name.lower()
    preferred_type = "generic"

    # Strong signal for Music Assistant
    if any(x in q_low for x in ["music", "song", "album", "artist", "play"]):
        preferred_type = "mass"

    elif intent == "play_media" and any(app in q_low for app in APP_PACKAGES):
        preferred_type = "android"

    elif intent in ["open_app"]:
        preferred_type = "android"

    elif intent in ["turn_on", "turn_off", "toggle"] or intent.startswith("nav_"):
        preferred_type = "remote"

    log.info(f"Smart Resolving '{query_name}' Intent '{intent}' Pref '{preferred_type}' Candidates {candidates}")

    # Best-match logic
    for eid, integration in candidates:
        if preferred_type == "mass" and ("mass" in eid or "music_assistant" in integration):
            return eid, integration

        if preferred_type == "android" and ("media_player" in eid and "mass" not in eid):
            return eid, integration

        if preferred_type == "remote" and ("remote" in eid or "androidtv" in integration):
            return eid, integration

    return candidates[0]


# ------------------------------------
# MEDIA COMMAND ROUTING
# ------------------------------------
async def handle_media_command(intent: str, query: str, entity_id: str, user_creds: dict, ha_collection, redis_client):
    q_low = query.lower()
    integration = "unknown"

    # Resolve entity if not provided
    if not entity_id:
        cleaned = q_low
        for p in ["turn on", "turn off", "toggle", "play", "stop", "open", "launch", "the", " on ", " please "]:
            cleaned = cleaned.replace(p, " ")

        cleaned = cleaned.strip()

        if not cleaned:
            entity_id = get_last_entity(redis_client, user_creds.get("user"))
            if not entity_id:
                return "Could not determine which device you mean."
        else:
            entity_id, integration = await smart_resolve_entity(cleaned, intent, ha_collection)
            if not entity_id:
                return f"I couldn't find a device named '{cleaned}'."

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
       
        # -------------------------
        # APP LAUNCH
        # -------------------------
        for app, pkg in APP_PACKAGES.items():
            if app in q_low:
                return await execute_ha_service(
                    "media_player", "play_media", entity_id, user_creds,
                    {"media_content_id": pkg, "media_content_type": "app"},
                    redis_client
                )

        # -------------------------
        # CLEAN TITLE (ROBUST)
        # -------------------------
        clean_title = q_low

        # remove common command words (word boundaries)
        clean_title = re.sub(r"\b(play|please|music|song|from|on|open|launch|playback)\b", " ", clean_title)
        # remove the definite article that often lingers
        clean_title = re.sub(r"\bthe\b", " ", clean_title)

        # Normalize whitespace early
        clean_title = re.sub(r"\s+", " ", clean_title).strip()

        # Try to remove device name in a tolerant way:
        # - take the raw entity tail (e.g. "office_tv_chrome_2")
        # - split into tokens and remove any contiguous token sequences that appear in the query
        raw_dev = entity_id.split(".")[-1] if entity_id else ""
        dev_tokens = [t for t in raw_dev.split("_") if t]
        variants = set()

        # build all contiguous token sequences (longest first)
        for i in range(len(dev_tokens)):
            for j in range(i + 1, len(dev_tokens) + 1):
                variants.add(" ".join(dev_tokens[i:j]))

        # also include the raw with underscores replaced
        if raw_dev:
            variants.add(raw_dev.replace("_", " "))

        # sort by length desc so we remove longest matches first
        for v in sorted(variants, key=lambda s: -len(s)):
            if not v:
                continue
            clean_title = re.sub(r"\b" + re.escape(v) + r"\b", " ", clean_title)

        # final whitespace normalization
        clean_title = re.sub(r"\s+", " ", clean_title).strip()

        # -------------------------
        # CONTENT TYPE LOGIC
        # -------------------------
        ctype = "music"

        is_music_request = any(x in q_low for x in ["music", "song", "artist"]) or "mass" in entity_id
        is_tv = any(x in entity_id.lower() for x in ["tv", "chromecast", "shield"])

        # If the device is a TV, default to video UNLESS it's clearly a music request
        if is_tv and not is_music_request:
            ctype = "video"

        # If entity explicitly points to Music Assistant, force music
        if "mass" in entity_id or "music_assistant" in integration:
            ctype = "music"

        # If the cleaned title looks like a URL, treat it as a url
        if "http" in clean_title:
            ctype = "url"

        service_data = {
            "media_content_id": clean_title,
            "media_content_type": ctype,
            "enqueue": "play"
        }

        # Auto wake
        state = await get_entity_state(entity_id, user_creds)
        if state in ["off", "unavailable"]:
            await execute_ha_service(domain, "turn_on", entity_id, user_creds, redis_client=redis_client)
            await asyncio.sleep(4.0)

        return await execute_ha_service("media_player", "play_media", entity_id, user_creds, service_data, redis_client)

    # -------------------------------------------------
    # TRANSPORT CONTROLS
    # -------------------------------------------------
    if intent == "stop_media":
        return await execute_ha_service("media_player", "media_stop", entity_id, user_creds, {}, redis_client)

    if intent == "media_next":
        return await execute_ha_service("media_player", "media_next_track", entity_id, user_creds, {}, redis_client)

    if intent == "media_previous":
        return await execute_ha_service("media_player", "media_previous_track", entity_id, user_creds, {}, redis_client)

    return None

