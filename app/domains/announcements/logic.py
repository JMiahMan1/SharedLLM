# app/domains/announcements/logic.py
import os
import json
import logging
import asyncio
import re
from typing import List, Dict, Optional, Union

from app.settings import log, GlobalResources, HA_URL, ANNOUNCEMENT_BLACKLIST, SERVER_URL
from app.domains.media.devices import smart_resolve_entity, get_available_media_players
from app.domains.shared import execute_ha_service
from app.domains.home.devices import get_entity_state

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
    
    # 1. Clean Message (Strip prefixes)
    # Common prefixes to remove so TTS doesn't say "Announce..."
    prefixes = [
        "announce that", "announce", 
        "tell everyone that", "tell everyone", 
        "broadcast that", "broadcast", 
        "shout that", "shout",
        "say"
    ]
    clean_message = message.strip()
    for p in prefixes:
        if clean_message.lower().startswith(p + " "):
            clean_message = clean_message[len(p):].strip()
        elif clean_message.lower().startswith(p): # Exact match or punctuation
            clean_message = clean_message[len(p):].strip()
            
    # Remove leading punctuation causing "An ounce" issues if partially stripped
    clean_message = clean_message.lstrip(' :,-')

    # 1b. Extract Emojis & Keyword Sounds
    emojis_found = []
    matched_sound = None
    
    if DEFAULT_SOUND_MAP:
        # Check for both Emojis AND Keywords (case-insensitive) in the original message OR the cleaned message
        # We check the cleaned message to avoid triggering on the command word itself if mapped (unlikely)
        msg_lower = clean_message.lower()
        
        for key, sound_path in DEFAULT_SOUND_MAP.items():
            # If key is emoji (non-ascii roughly) or keyword
            if key.lower() in msg_lower:
                # If it's a keyword like 'dinner', we don't necessarily remove it from text, 
                # but we trigger the sound.
                # If it's an emoji, we usually remove it.
                is_emoji = not key.isascii()
                
                matched_sound = sound_path
                if is_emoji:
                    clean_message = clean_message.replace(key, "")
                    emojis_found.append(key)
                
                # We stop at first match for now to avoid chaos, or could chain them.
                # User asked for "Kitchen" -> Bell? No, "Dinner" -> Bell.
                break
            
    clean_message = clean_message.strip()
    if not clean_message and not audio_url:
        clean_message = "Announcement"

    
    # ... (Target Resolution logic is same)
    # 2. Resolve Targets
    target_entities = []
    
    if not target or target.lower() in ["all", "everyone", "broadcast", "everywhere", "house"]:
        # BROADCAST MODE
        all_players = await get_available_media_players(user_creds)
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
                    if isinstance(item, str) and item not in ANNOUNCEMENT_BLACKLIST:
                        target_entities.append(item)
                    elif isinstance(item, (list, tuple)) and len(item) > 0 and item[0] not in ANNOUNCEMENT_BLACKLIST:
                        target_entities.append(item[0])
            elif isinstance(resolved, tuple) and resolved[0] not in ANNOUNCEMENT_BLACKLIST:
                 target_entities.append(resolved[0])
            elif isinstance(resolved, str) and resolved not in ANNOUNCEMENT_BLACKLIST:
                 target_entities.append(resolved)

    if not target_entities:
        log.warning(f"No target devices found for announcement: {target}")
        return {"status": "FAILURE", "message": "Could not find any devices to announce on."}

    # 3. Execution (Throttled & Filtered)
    results = []
    
    # Semaphore to limit concurrent calls to HA to avoid 500 errors
    sem = asyncio.Semaphore(2)  # Adjust concurrency limit if needed

    # Pre-fetch capabilities to filter unsupported devices
    from app.domains.media.devices import get_device_capabilities
    
    capable_entities = []
    for eid in target_entities:
         caps = await get_device_capabilities(eid, user_creds, GlobalResources.redis_client)
         if caps.get("has_play_media") or caps.get("domain") == "group": 
             # Groups might not report capabilities correctly but usually handle relaying
             capable_entities.append(eid)
         else:
             log.warning(f"Skipping announcement for {eid}: Device does not support 'play_media'.")

    if not capable_entities:
        return {"status": "FAILURE", "message": "No capable devices found for announcement."}

    async def _announce_one(entity_id):
        async with sem:
            try:
                # [SMART ANNOUNCE] 
                # Check device capabilities to enable 'announce' feature (restore state)
                # User Request: Ensure every device returns to previous state.
                caps = await get_device_capabilities(entity_id, user_creds, GlobalResources.redis_client)
                features = caps.get("supported_features", 0)
                
                # Check Bit 1048576 (ANNOUNCE)
                supports_announce = bool(features & 1048576)
                should_announce = supports_announce
                
                # Optional: Get state just for logging
                current_state = await get_entity_state(entity_id, user_creds)
                log.info(f"Smart Announce for {entity_id}: state={current_state}, supports_announce={supports_announce} -> announce={should_announce}")

                # [MANUAL STATE RESTORE]
                # If device is OFF and 'announce' not supported, we must:
                # 1. Turn ON (if supported)
                # 2. Wait
                # 3. Play
                # 4. Turn OFF (restore state)
                did_turn_on = False
                if not should_announce and current_state == "off":
                    if features & 128: # SUPPORT_TURN_ON
                         log.info(f"Device {entity_id} is OFF. Turning ON manually...")
                         await execute_ha_service("media_player", "turn_on", entity_id, user_creds, {}, GlobalResources.redis_client)
                         did_turn_on = True
                         await asyncio.sleep(4) # Wait for TV/Speaker to wake up
                    else:
                         log.warning(f"Device {entity_id} is OFF and does not support turning on.")

                # Play Sound Effect (if any)
                if matched_sound:
                    # Play media (notification sound)
                    log.info(f"Playing sound {matched_sound} on {entity_id}")
                    svc_data = {
                         "media_content_id": f"{HA_URL.rstrip('/')}{matched_sound}",
                         "media_content_type": "music"
                    }
                    if should_announce:
                        svc_data["announce"] = True
                        
                    await execute_ha_service(
                        "media_player", "play_media", entity_id, user_creds,
                        svc_data,
                        GlobalResources.redis_client
                    )
                    # Small delay for sound to start/finish
                    await asyncio.sleep(2)

                # Play TTS OR Audio File
                if audio_url:
                     # INTERCOM MODE (Recorded Voice)
                     final_url = audio_url
                     # Naive absolute URL construction if just a path
                     if audio_url.startswith("/") and "http" not in audio_url:
                         # Prepend SERVER_URL to make it a valid absolute URL for Cast devices
                         final_url = f"{SERVER_URL.rstrip('/')}{audio_url}"
                         
                     log.info(f"Playing Intercom Audio '{final_url}' on {entity_id}")
                     
                     svc_data = {
                         "media_content_id": final_url,
                         "media_content_type": "music"
                     }
                     if should_announce:
                         svc_data["announce"] = True
                         
                     res = await execute_ha_service(
                        "media_player", "play_media", entity_id, user_creds,
                        svc_data,
                        GlobalResources.redis_client
                    )
                else:
                    # TTS MODE - Use media-source://tts/tts.piper to enable 'announce' flag support
                    import urllib.parse
                    encoded_msg = urllib.parse.quote(clean_message)
                    media_id = f"media-source://tts/tts.piper?message={encoded_msg}"
                    
                    log.info(f"Playing TTS '{clean_message}' on {entity_id} via Piper (media-source)")
                    
                    svc_data = {
                        "media_content_id": media_id,
                        "media_content_type": "music"
                    }
                    if should_announce:
                        svc_data["announce"] = True

                    res = await execute_ha_service(
                        "media_player", "play_media", entity_id, user_creds,
                        svc_data,
                        GlobalResources.redis_client
                    )

                # [RESTORE OFF STATE]
                # Also handle "Sound After" (e.g. Dinner bell at end)
                # We need to wait for playback anyway if restoring state.
                
                # Estimate duration
                duration = max(5, len(clean_message or "") / 12)
                if is_audio_file:
                    duration = 10 
                
                # If we need to restore state OR play sound after, we wait.
                # Check for specific keywords that need "Double Ding" (Before & After)
                # For now, just 'dinner' triggers this behavior per user request
                play_after = matched_sound and ("dinner" in clean_message.lower())
                
                if did_turn_on or play_after:
                    log.info(f"Waiting {duration:.1f}s for playback (Restore: {did_turn_on}, SoundData: {play_after})...")
                    await asyncio.sleep(duration)
                    
                    if play_after:
                        # Play sound again
                        log.info(f"Playing suffix sound {matched_sound} on {entity_id}")
                        svc_data["media_content_id"] = f"{HA_URL.rstrip('/')}{matched_sound}"
                        svc_data["media_content_type"] = "music" # Reset for sound
                        if "announce" in svc_data:
                            svc_data["announce"] = True
                            
                        await execute_ha_service(
                             "media_player", "play_media", entity_id, user_creds,
                             svc_data,
                             GlobalResources.redis_client
                        )
                        # Wait for sound to finish before turning off?
                        if did_turn_on:
                            await asyncio.sleep(2)

                    if did_turn_on:
                         await execute_ha_service("media_player", "turn_off", entity_id, user_creds, {}, GlobalResources.redis_client)

                return True
            except Exception as e:
                log.error(f"Failed to announce on {entity_id}: {e}")
                return False

    # Run tasks with throttling on CAPABLE entities only
    tasks = [_announce_one(eid) for eid in capable_entities]
    await asyncio.gather(*tasks)
    
    return {"status": "SUCCESS", "message": f"Announced to {len(capable_entities)} capable devices."}
