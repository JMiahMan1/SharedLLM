# app/domains/announcements/logic.py
import os
import json
import logging
import asyncio
import re
from typing import List, Dict, Optional, Union

from app.settings import log, GlobalResources, HA_URL, ANNOUNCEMENT_BLACKLIST
from app.domains.media.devices import smart_resolve_entity, get_available_media_players
from app.domains.shared import execute_ha_service

SOUNDS_FILE = "/app/data/announcement_sounds.json"
DEFAULT_SOUND_MAP = {}

# Load sounds on module import or first use
try:
    if os.path.exists(SOUNDS_FILE):
        with open(SOUNDS_FILE, "r") as f:
            data = json.load(f)
            # Handle list of dicts or single dict
            if isinstance(data, list) and len(data) > 0:
                DEFAULT_SOUND_MAP = data[0]
            elif isinstance(data, dict):
                DEFAULT_SOUND_MAP = data
except Exception as e:
    log.error(f"Failed to load announcement sounds: {e}")

async def process_announcement(message: str, target: str = None, user_creds: dict = None, audio_url: str = None):
    """
    Process an announcement request.
    1. Identify emoji sound effects.
    2. Resolve target devices (specific or broadcast).
    3. Play sound effect (if any).
    4. Play TTS message OR Audio URL.
    """
    log.info(f"Processing Announcement: '{message}' (Audio: {bool(audio_url)}) to target: '{target}'")
    
    # 1. Extract Emojis & Sounds
    # ... (Keep existing logic)
    emojis_found = []
    clean_message = message
    
    # Simple regex for emojis (this is a basic range, might need expansion)
    # We prioritize matching keys in our sound map
    matched_sound = None
    
    if DEFAULT_SOUND_MAP:
        for emoji_char, sound_path in DEFAULT_SOUND_MAP.items():
            if emoji_char in message:
                emojis_found.append(emoji_char)
                matched_sound = sound_path
                clean_message = clean_message.replace(emoji_char, "")
            
    clean_message = clean_message.strip()
    if not clean_message and not audio_url:
        clean_message = "Announcement" # Fallback if only emoji was sent and no audio
    
    # ... (Target Resolution logic is same)
    # 2. Resolve Targets
    target_entities = []
    
    if not target or target.lower() in ["all", "everyone", "broadcast", "everywhere", "house"]:
        # BROADCAST MODE
        all_players = await get_available_media_players(GlobalResources.ha_collection)
        # Filter blacklist
        for p in all_players:
            if p not in ANNOUNCEMENT_BLACKLIST:
                target_entities.append(p)
        log.info(f"Announcement Broadcast to {len(target_entities)} devices.")
    else:
        # TARGETED MODE
        # Use smart resolver
        # We use 'play_media' intent to ensure we prioritize media players/speakers
        resolved = await smart_resolve_entity(target, "play_media", GlobalResources.ha_collection)
        
        # smart_resolve_entity can return different structures, normalize it
        # Usually returns list of entities or list of tuples
        if resolved:
            if isinstance(resolved, list):
                for item in resolved:
                    if isinstance(item, str):
                        target_entities.append(item)
                    elif isinstance(item, (list, tuple)) and len(item) > 0:
                        target_entities.append(item[0])
            elif isinstance(resolved, tuple):
                 target_entities.append(resolved[0])

    if not target_entities:
        log.warning(f"No target devices found for announcement: {target}")
        return {"status": "FAILURE", "message": "Could not find any devices to announce on."}

    # 3. Execution (Parallel)
    results = []
    
    async def _announce_one(entity_id):
        try:
            # Play Sound Effect (if any)
            if matched_sound:
                # Play media (notification sound)
                log.info(f"Playing sound {matched_sound} on {entity_id}")
                await execute_ha_service(
                    "media_player", "play_media", entity_id, user_creds,
                    {
                        "media_content_id": f"{HA_URL.rstrip('/')}{matched_sound}",
                        "media_content_type": "music",
                        "announce": True 
                    },
                    GlobalResources.redis_client
                )
                # Small delay for sound to start/finish
                await asyncio.sleep(2)

            # Play TTS OR Audio File
            if audio_url:
                 # INTERCOM MODE (Recorded Voice)
                 # We need to ensure audio_url is absolute or accessible by HA.
                 # If it starts with /static, prepend our own ingress URL if known, 
                 # OR assume HA can access it if we use full URL.
                 # For now, we assume the caller provided a usable URL or path.
                 # If relative /static, we might need to prepend HA_URL if using HA proxy? No, HA needs to reach US.
                 # If this app is sidecar, HA might reach it via host IP. 
                 # We'll use the raw URL passed for now, assuming the uploader constructed it correctly relative to HA or robustly.
                 
                 final_url = audio_url
                 # Naive absolute URL construction if just a path
                 if audio_url.startswith("/") and "http" not in audio_url:
                     # Attempt to discover own storage URL? 
                     # Fallback: Just send it, maybe HA is on same host volume mount?
                     # Better: Prepend a configured MIDDELWARE_EXTERNAL_URL if available.
                     # Defaulting to sending valid http if we can.
                     # For now, let's assume raw string is what HA needs (e.g. /local/... or http://...)
                     pass
                     
                 log.info(f"Playing Intercom Audio '{final_url}' on {entity_id}")
                 res = await execute_ha_service(
                    "media_player", "play_media", entity_id, user_creds,
                    {
                        "media_content_id": final_url,
                        "media_content_type": "music",
                        "announce": True
                    },
                    GlobalResources.redis_client
                )
            else:
                # TTS MODE
                log.info(f"Playing TTS '{clean_message}' on {entity_id}")
                res = await execute_ha_service(
                    "tts", "google_translate_say", entity_id, user_creds,
                    {
                        "message": clean_message,
                        "cache": True
                    },
                    GlobalResources.redis_client
                )
            return res
        except Exception as e:
            log.error(f"Error announcing on {entity_id}: {e}")
            return {"status": "FAILURE", "entity": entity_id, "error": str(e)}

    # Run all announcements in parallel
    tasks = [_announce_one(e) for e in target_entities]
    results = await asyncio.gather(*tasks)
    
    success_count = sum(1 for r in results if r.get("status") == "SUCCESS")
    return {
        "status": "SUCCESS" if success_count > 0 else "FAILURE",
        "message": f"Announced on {success_count} devices.",
        "details": results
    }
