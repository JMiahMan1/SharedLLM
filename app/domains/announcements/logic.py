# app/domains/announcements/logic.py
import os
import json
import asyncio
import re

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
    
    # 1.5. If target is in the message, strip it too to avoid the TTS saying "on the Office TV"
    if target and target.lower() in clean_message.lower():
        # Remove "on the [target]", "to the [target]", etc.
        clean_message = re.sub(rf"\b(on|to|in|at)\s+(the\s+)?{re.escape(target.lower())}\b", "", clean_message, flags=re.IGNORECASE)
        # Also strip just the target name
        clean_message = clean_message.replace(target, "").replace(target.lower(), "").strip()
        # Clean up double spaces
        clean_message = re.sub(r'\s+', ' ', clean_message).strip()

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
    
    # Semaphore to limit concurrent calls to HA to avoid 500 errors
    sem = asyncio.Semaphore(3)
    
    # Per-device timeout to prevent broadcast stalling on unresponsive devices
    DEVICE_TIMEOUT = 90  # seconds

    # Pre-fetch capabilities to filter unsupported devices
    from app.domains.media.devices import get_device_capabilities
    
    is_broadcast = not target or target.lower() in ["all", "everyone", "broadcast", "everywhere", "house"]
    
    capable_entities = []
    for eid in target_entities:
         caps = await get_device_capabilities(eid, user_creds, GlobalResources.redis_client)
         features = caps.get("supported_features", 0)
         has_play_media = caps.get("has_play_media") or caps.get("domain") == "group"
         
         if not has_play_media:
              log.warning(f"Skipping announcement for {eid}: Device does not support 'play_media'.")
              continue
         
         # For broadcast: also check if the device is reachable and can actually play audio
         if is_broadcast:
              current_state = await get_entity_state(eid, user_creds)
              supports_announce = bool(features & 1048576)  # ANNOUNCE flag
              supports_turn_on = bool(features & 128)       # SUPPORT_TURN_ON flag
              
              if current_state in ["unavailable", "unknown", None]:
                   log.warning(f"Skipping broadcast for {eid}: Device is {current_state} (unreachable).")
                   continue
              
              if current_state in ["off", "standby"] and not supports_announce and not supports_turn_on:
                   log.warning(f"Skipping broadcast for {eid}: Device is {current_state} and cannot be turned on or announce.")
                   continue
              
              log.info(f"Broadcast eligible: {eid} (state={current_state}, announce={supports_announce}, turn_on={supports_turn_on})")
         
         capable_entities.append(eid)

    if not capable_entities:
        return {"status": "FAILURE", "message": "No capable devices found for announcement."}


    async def _announce_one(entity_id):
        async with sem:
            # Initialization
            sibling_turned_on = None  # (sib_id, sib_orig_state, sib_meta)
            did_turn_on = False
            integration_instance = None
            should_announce = False
            
            try:
                # 1. Get capabilities and state
                caps = await get_device_capabilities(entity_id, user_creds, GlobalResources.redis_client)
                features = caps.get("supported_features", 0)
                supports_announce = bool(features & 1048576)
                should_announce = supports_announce
                
                current_state = await get_entity_state(entity_id, user_creds)
                log.info(f"Smart Announce for {entity_id}: state={current_state}, supports_announce={supports_announce} -> announce={should_announce}")

                # 2. [GROUP-AWARE POWER MANAGEMENT]
                if current_state in ["unavailable", "unknown"]:
                    from app.domains.media.devices import find_group_sibling
                    log.info(f"Device {entity_id} is {current_state}. Attempting group-aware power sync...")
                    
                    def is_power_controller(meta):
                        integ = meta.get("integration", "").lower()
                        return integ in ["androidtv", "webostv", "samsungtv", "braviatv", "roku", "esphome"]
                    
                    sibling = await find_group_sibling(entity_id, is_power_controller, return_meta=True)
                    
                    # Fallback: group_name lookup
                    if not sibling:
                        try:
                            ha_col = GlobalResources.ha_collection
                            our_doc = ha_col.get(ids=[entity_id], include=["metadatas"])
                            if our_doc and our_doc.get("metadatas"):
                                our_group_name = our_doc["metadatas"][0].get("group_name", "")
                                if our_group_name:
                                    all_docs = ha_col._collection.get(where={"group_name": our_group_name}, include=["metadatas"])
                                    if all_docs and all_docs.get("metadatas"):
                                        for meta in all_docs["metadatas"]:
                                            cid = meta.get("entity_id")
                                            if cid != entity_id and is_power_controller(meta):
                                                sibling = (cid, meta)
                                                break
                        except: pass

                    if sibling:
                        sibling_id, sibling_meta = sibling
                        sibling_state = await get_entity_state(sibling_id, user_creds)
                        if sibling_state in ["off", "standby", "idle", "unavailable"]:
                            try:
                                from app.domains.media.integrations.factory import IntegrationFactory
                                handler = IntegrationFactory.get_handler(sibling_meta.get("integration", "standard"))
                                if handler: await handler.turn_on(sibling_id, user_creds, redis_client=GlobalResources.redis_client)
                                else: await execute_ha_service("media_player", "turn_on", sibling_id, user_creds, {}, GlobalResources.redis_client)
                                sibling_turned_on = (sibling_id, sibling_state, sibling_meta)
                            except:
                                await execute_ha_service("media_player", "turn_on", sibling_id, user_creds, {}, GlobalResources.redis_client)
                                sibling_turned_on = (sibling_id, sibling_state, sibling_meta)
                            
                            # Polling
                            for _ in range(10):
                                await asyncio.sleep(2)
                                current_state = await get_entity_state(entity_id, user_creds)
                                if current_state not in ["unavailable", "unknown"]: break

                # 3. [MANUAL STATE RESTORE] (Main Device)
                if not should_announce and current_state in ["off", "idle", "standby"]:
                    if features & 128:
                        log.info(f"Device {entity_id} is {current_state}. Turning ON manually...")
                        from app.domains.media.integrations.factory import IntegrationFactory
                        integration_instance = IntegrationFactory.get_handler(caps.get("integration", "standard"))
                        if integration_instance: await integration_instance.turn_on(entity_id, user_creds, redis_client=GlobalResources.redis_client)
                        else: await execute_ha_service("media_player", "turn_on", entity_id, user_creds, {}, GlobalResources.redis_client)
                        did_turn_on = True
                        await asyncio.sleep(4)

                # [Fix: Audibility] Re-enable 'announce' flag as requested by user.
                # Silence was likely due to device not being "ready" to stream.
                if (did_turn_on or sibling_turned_on):
                    log.info(f"Device just woke up. Polling {entity_id} for readiness (max 30s)...")
                    # Increased to 15 iterations (30s) to give Cast integration time to wake up
                    for _ in range(15):
                        await asyncio.sleep(2)
                        st = await get_entity_state(entity_id, user_creds)
                        if st not in ["unavailable", "unknown", "off"]:
                            log.info(f"Device {entity_id} is now {st}. Ready for announcement.")
                            break
                    else:
                        log.warning(f"Device {entity_id} still {current_state} after 30s. Proceeding anyway...")

                # 3.5 [VOLUME CONTROL]
                # Ensure the device is audible (User reported silent announcement)
                try:
                    log.info(f"Setting volume for {entity_id} to 0.6 before announcement")
                    await execute_ha_service("media_player", "volume_set", entity_id, user_creds, {"volume_level": 0.6}, GlobalResources.redis_client)
                    await asyncio.sleep(2) # Wait for volume to apply
                except Exception as ve:
                    log.warning(f"Failed to set volume for {entity_id}: {ve}")

                # 4. Sound Before
                if matched_sound:
                    log.info(f"Playing sound {matched_sound} on {entity_id}")
                    sound_url = matched_sound if matched_sound.startswith("http") else (f"{SERVER_URL.rstrip('/')}{matched_sound}" if matched_sound.startswith("/static") else f"{HA_URL.rstrip('/')}{matched_sound}")
                    svc_data = {"media_content_id": sound_url, "media_content_type": "music"}
                    if should_announce: svc_data["announce"] = True
                    await execute_ha_service("media_player", "play_media", entity_id, user_creds, svc_data, GlobalResources.redis_client)
                    await asyncio.sleep(2)

                # 5. Play Main Content
                if audio_url:
                    final_url = audio_url if ("http" in audio_url) else f"{SERVER_URL.rstrip('/')}{audio_url}"
                    svc_data = {"media_content_id": final_url, "media_content_type": "music"}
                    if should_announce: svc_data["announce"] = True
                    await execute_ha_service("media_player", "play_media", entity_id, user_creds, svc_data, GlobalResources.redis_client)
                else:
                    import urllib.parse
                    media_id = f"media-source://tts/tts.piper?message={urllib.parse.quote(clean_message)}"
                    svc_data = {"media_content_id": media_id, "media_content_type": "music"}
                    if should_announce: svc_data["announce"] = True
                    await execute_ha_service("media_player", "play_media", entity_id, user_creds, svc_data, GlobalResources.redis_client)

                # 6. Sound After / Wait
                duration = max(5, len(clean_message or "") / 12) if not audio_url else 10
                play_after = matched_sound and ("dinner" in clean_message.lower())
                
                if did_turn_on or play_after or sibling_turned_on:
                    log.info(f"Waiting {duration:.1f}s for playback...")
                    await asyncio.sleep(duration)
                    if play_after:
                        sound_url = matched_sound if matched_sound.startswith("http") else (f"{SERVER_URL.rstrip('/')}{matched_sound}" if matched_sound.startswith("/static") else f"{HA_URL.rstrip('/')}{matched_sound}")
                        svc_data = {"media_content_id": sound_url, "media_content_type": "music"}
                        if should_announce: svc_data["announce"] = True
                        await execute_ha_service("media_player", "play_media", entity_id, user_creds, svc_data, GlobalResources.redis_client)
                        await asyncio.sleep(2)

                return True

            except Exception as e:
                log.error(f"Failed to announce on {entity_id}: {e}")
                return False
                
            finally:
                # 7. [ROBUST STATE RESTORATION]
                if did_turn_on:
                    log.info(f"Restoring main device {entity_id} to OFF")
                    try:
                        if integration_instance: await integration_instance.turn_off(entity_id, user_creds, redis_client=GlobalResources.redis_client)
                        else: await execute_ha_service("media_player", "turn_off", entity_id, user_creds, {}, GlobalResources.redis_client)
                    except: pass
                
                if sibling_turned_on:
                    sib_id, sib_orig_state, sib_meta = sibling_turned_on
                    log.info(f"[Group Power] Restoring sibling {sib_id} to {sib_orig_state}")
                    try:
                        from app.domains.media.integrations.factory import IntegrationFactory
                        handler = IntegrationFactory.get_handler(sib_meta.get("integration", "standard"))
                        if handler: await handler.turn_off(sib_id, user_creds, redis_client=GlobalResources.redis_client)
                        else: await execute_ha_service("media_player", "turn_off", sib_id, user_creds, {}, GlobalResources.redis_client)
                    except: pass


    # Run tasks with throttling on CAPABLE entities only, with per-device timeout
    # SHIELDED: Ensure process continues even if parent task is cancelled
    async def _safe_announce(eid):
        try:
            # Shielding ensures the internal _announce_one continues its work
            # (including restoration) even if this outer task is cancelled.
            return await asyncio.wait_for(asyncio.shield(_announce_one(eid)), timeout=DEVICE_TIMEOUT)
        except asyncio.TimeoutError:
            log.error(f"Announcement timed out for {eid} after {DEVICE_TIMEOUT}s")
            return False
        except asyncio.CancelledError:
            log.warning(f"Announcement task for {eid} was cancelled, but shielded process continues.")
            # We return True so the UI doesn't look like a total failure,
            # as the audio will likely still play.
            return True
    
    tasks = [_safe_announce(eid) for eid in capable_entities]
    results = await asyncio.gather(*tasks)
    
    succeeded = sum(1 for r in results if r)
    failed = len(results) - succeeded
    msg = f"Announced to {succeeded}/{len(capable_entities)} devices."
    if failed:
        msg += f" ({failed} failed or timed out.)"
    
    return {"status": "SUCCESS", "message": msg}
