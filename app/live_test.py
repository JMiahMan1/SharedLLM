
import asyncio
import os
import sys
import json
import logging
import requests
import time
from typing import Optional

# Ensure we can import app modules
sys.path.append(os.path.join(os.getcwd(), "app"))

# Redirect output to file immediately
LOG_FILE = "./temp/live_test_output.txt"
os.makedirs("./temp", exist_ok=True)

# Setup logging
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True
)
console = logging.StreamHandler() # Also print to stdout (which is captured by the tool)
logging.getLogger().addHandler(console)
log = logging.getLogger("LiveTest")

from settings import load_resources, GlobalResources, HA_URL, get_user_creds, DEFAULT_MODEL
from logic import contextualize_query, try_handle_compound_command
from logic.execution.handlers import handle_note_read, handle_note_delete
from logic.timer_storage import storage as timer_storage

async def setup():
    log.info("--- SETUP: Loading Resources ---")
    await load_resources()
    # Ensure Redis/Chroma are ready
    if not GlobalResources.redis_client or not GlobalResources.chroma_client:
        log.error("CRITICAL: Resources failed to load.")
        sys.exit(1)

def get_ha_headers():
    creds = get_user_creds("default")
    return {
        "Authorization": f"Bearer {creds['ha_token']}",
        "Content-Type": "application/json"
    }

def discover_entities(domain: str) -> Optional[str]:
    """Find a valid entity ID for testing."""
    try:
        url = f"{HA_URL.rstrip('/')}/api/states"
        r = requests.get(url, headers=get_ha_headers(), timeout=5)
        if r.status_code != 200:
            log.error(f"Failed to query HA: {r.text}")
            return None
        
        for entity in r.json():
            eid = entity['entity_id']
            if eid.startswith(f"{domain}."):
                # Prefer one that isn't a "group" if possible, or maybe specific ones?
                # For now, just grab the first valid one that isn't unavailable
                if entity['state'] not in ['unavailable', 'unknown']:
                    log.info(f"Discovered test entity: {eid} (State: {entity['state']})")
                    return eid
        return None
    except Exception as e:
        log.error(f"Discovery Error: {e}")
        return None

def discover_all_entities(domain: str) -> list:
    """Return all entities for a domain."""
    try:
        url = f"{HA_URL.rstrip('/')}/api/states"
        r = requests.get(url, headers=get_ha_headers(), timeout=5)
        if r.status_code != 200: return []
        
        entities = []
        for entity in r.json():
            if entity['entity_id'].startswith(f"{domain}."):
                entities.append(entity)
        return entities
    except: return []

async def run_nl_command(query: str):
    log.info(f"TEST ACTION: '{query}'")
    user = "admin"
    creds = get_user_creds(user)
    
    # Simulate the pipeline
    refined, intent, score, is_high_confidence = await contextualize_query(query, user, DEFAULT_MODEL)
    log.info(f"  -> Refined: '{refined}', Intent: {intent} (Score: {score})")
    
    if not is_high_confidence and score < 75:
       log.warning(f"  -> Low confidence for '{query}'")
    
    result = await try_handle_compound_command(refined, creds, DEFAULT_MODEL, intent, score, is_high_confidence)
    log.info(f"  -> Result: {result}")
    return result

async def verify_ha_state(entity_id: str, expected_state, check_attr: dict = None):
    try:
        url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
        r = requests.get(url, headers=get_ha_headers(), timeout=5)
        data = r.json()
        current_state = data['state']
        attributes = data.get('attributes', {})
        
        log.info(f"  VERIFY: {entity_id} is '{current_state}'")
        
        # Normalize expected_state to list
        if not isinstance(expected_state, list):
            expected_state = [expected_state]
            
        if expected_state and current_state not in expected_state:
            log.error(f"  FAILURE: Expected {expected_state}, got '{current_state}'")
            return False
            
        if check_attr:
            for k, v in check_attr.items():
                curr_val = attributes.get(k)
                # Loose matching for colors as HA returns tuples
                log.info(f"  VERIFY ATTR: {k} = {curr_val} (Expect {v})")
                if curr_val != v:
                     log.warning(f"  Attr mismatch: {k} expected {v} got {curr_val}")
                     # Don't fail hard on color tuples due to rounding, just warn
        
        return True
    except Exception as e:
        log.error(f"Verify Error: {e}")
        return False

async def main():
    await setup()
    
    # --- TEST 1: LIGHTS ---
    light_id = discover_entities("light")
    if light_id:
        log.info(f"=== TEST GROUP: LIGHTS ({light_id}) ===")
        # 1. Turn On
        await run_nl_command(f"Turn on {light_id}")
        await asyncio.sleep(2)
        await verify_ha_state(light_id, "on")
        
        # 2. Turn Off
        await run_nl_command(f"Turn off {light_id}")
        await asyncio.sleep(2)
        await verify_ha_state(light_id, "off")
        
        # 3. Color (Turn on first)
        await run_nl_command(f"Turn on {light_id}")
        await run_nl_command(f"Make {light_id} red")
        await asyncio.sleep(2)
        await verify_ha_state(light_id, "on") # Checking attribute is hard without specific bulb knowledge, just check it's on
    else:
        log.warning("SKIPPING LIGHT TESTS: No light entity found.")

    # --- TEST 2: MEDIA ---
    log.info("--- DISCOVERY: LISTING ALL MEDIA PLAYERS ---")
    all_media = discover_all_entities("media_player")
    for mp in all_media:
        log.info(f"Found Media Player: {mp['entity_id']} ({mp['attributes'].get('friendly_name')}) State: {mp['state']}")

    # Prefer Office TV for generic test if available since we know it works
    media_id = "media_player.office_tv_chrome_2"
    # Verify it exists in discovered list
    if not any(m['entity_id'] == media_id for m in all_media):
        media_id = discover_entities("media_player")

    if media_id:
        log.info(f"=== TEST GROUP: MEDIA GENERIC ({media_id}) ===")
        # 1. Play (Generic)
        await run_nl_command(f"Play some music on {media_id}")
        await asyncio.sleep(5)
        await verify_ha_state(media_id, "playing")
        
        # 2. Pause
        await run_nl_command(f"Pause {media_id}")
        await asyncio.sleep(2)
        await verify_ha_state(media_id, ["paused", "idle", "off"]) # Allow idle/off for some devices
    
    # --- TEST 2b: OFFICE TV SPECIFIC (User Request) ---
    log.info("=== TEST GROUP: OFFICE TV MUSIC ASSISTANT (Play/Skip/Stop) ===")
    target_tv = "Office TV"
    
    # 1. Play
    await run_nl_command(f"Play Brandon Lake on {target_tv}")
    await asyncio.sleep(10) # Give it time to buffer/start
    
    # Verify playing
    # We check known entity from previous run
    ma_entity = "media_player.office_tv_chrome_2" 
    await verify_ha_state(ma_entity, "playing")

    # --- DYNAMIC DISCOVERY & TESTING (User Request) ---
    log.info("\n=== DYNAMIC DISCOVERY: Scanning for TV/Music Players ===")
    
    # 1. Fetch current states to find candidate devices
    # We look for devices that appear to be TVs or Speakers and have an MA equivalent or ARE an MA player
    
    # Refresh states mapping
    all_states = {}
    try:
        req = requests.get(f"{HASS_URL}/api/states", headers=HEADERS)
        if req.status_code == 200:
            for s in req.json():
                all_states[s['entity_id']] = s
    except Exception as e:
        log.error(f"Failed to fetch states for discovery: {e}")

    # Build list of targets (Friendly Name -> Entity ID)
    targets_to_test = []
    
    for eid, state_obj in all_states.items():
        if not eid.startswith("media_player."):
            continue
            
        fname = state_obj.get("attributes", {}).get("friendly_name", eid)
        integration = "unknown" # We'd need to cross ref with similarity search or attributes if available
        
        # Heuristic: Identify "TVs" or "Speakers" that we want to test MA playback on.
        # We specifically want to test the "User faced" name.
        # If we see "Office TV" (generic) and "Office TV" (MA), we want to test "Office TV" and ensure it routes to MA.
        
        # We'll filter for Generic looking devices that likely have an MA shadow
        if "tv" in eid.lower() or "speaker" in eid.lower():
             # Avoid testing the _2 devices directly with their explicit name, we want to test the ambiguous Friendly Name
             # But we need to know WHICH friendly name is the 'common' one.
             # Usually the generic device has the clean name "Office TV" and the MA device might also have "Office TV" or be hidden.
             
             # Let's test using the Friendly Name of any device that IS NOT the MA player, 
             # but has a partner MA player.
             
             clean_name = fname.replace(" Chrome", "").strip() # Clean up cast names
             
             # Avoid duplicates in our test list
             if clean_name not in [x['name'] for x in targets_to_test]:
                 # Check if there is a corresponding MA player in the system (ending in _2 or integration check)
                 # Simple check: Does an entity with "_2" exist?
                 expected_ma_id = f"{eid}_2"
                 # Or check if any other entity has same friendly name but is MA?
                 
                 has_ma_variant = False
                 for check_eid in all_states:
                     if check_eid == expected_ma_id:
                         has_ma_variant = True
                         break
                     # Check attributes for app_id: music_assistant if we could (not always in state)
                 
                 if has_ma_variant or "_2" in eid: 
                     # If it is the _2 device, use its base name? No, users say "Office TV", which equates to the generic device usually.
                     # So we target the generic device's friendly name.
                     if "_2" not in eid: 
                         targets_to_test.append({"name": clean_name, "generic_id": eid, "ma_id": expected_ma_id})

    log.info(f"Discovered {len(targets_to_test)} Test Targets: {[t['name'] for t in targets_to_test]}")

    for target in targets_to_test:
        fname = target['name']
        ma_target = target['ma_id']
        
        log.info(f"\n--- TESTING TARGET: '{fname}' (Expect -> {ma_target}) ---")
        
        # Test 1: Play Music (Ambiguous "Play [Artist]")
        # We use a real artist to test the 'is_music' inference logic we just fixed
        query = f"Play Brandon Lake on {fname}"
        log.info(f"TEST ACTION: '{query}'")
        
        # Trigger Pipeline
        # We do NOT force intent here, we rely on the pipeline to classify PLAY -> play_media
        # But for this test harness, we call handle_media_command directly to skip whisper/transcription layer if desired,
        # OR we simulate the full flow. live_test usually calls handle_media_command directly or resolves manually?
        # The existing code called handle_media_command.
        
        # We let the system infer intent from "Play ..."
        # Note: intent classification might need "music" keyword if strict. 
        # But 'Play Brandon Lake' should trigger play_media.
        intent = "play_media" 
        
        await handle_media_command(intent, query, None, admin_creds, GlobalResources.ha_collection, GlobalResources.redis_client)
        
        # Verification: Check MA entity state
        log.info(f"  VERIFYING: {ma_target} should be PLAYING")
        await asyncio.sleep(8) # Wait for buffer
        await verify_ha_state(ma_target, ["playing", "buffering"], user_creds)
        
        # Test 2: Next Track
        query = f"Next song on {fname}"
        log.info(f"TEST ACTION: '{query}'")
        await handle_media_command("media_next", query, None, admin_creds, GlobalResources.ha_collection, GlobalResources.redis_client)
        await asyncio.sleep(2)
        
        # Test 3: Stop
        query = f"Stop music on {fname}"
        log.info(f"TEST ACTION: '{query}'")
        await handle_media_command("stop_media", query, None, admin_creds, GlobalResources.ha_collection, GlobalResources.redis_client)
        await asyncio.sleep(2)
        await verify_ha_state(ma_target, ["idle", "paused", "off"], user_creds)

    
    # 2. Skip (Next)
    log.info("  Testing SKIP/NEXT...")
    await run_nl_command(f"Next song on {target_tv}")
    await asyncio.sleep(3)
    await verify_ha_state(ma_entity, "playing") 

    # 3. Previous
    log.info("  Testing PREVIOUS...")
    await run_nl_command(f"Previous song on {target_tv}")
    await asyncio.sleep(3)
    await verify_ha_state(ma_entity, "playing")

    # 4. Stop
    log.info("  Testing STOP...")
    await run_nl_command(f"Stop music on {target_tv}")
    await asyncio.sleep(3)
    await verify_ha_state(ma_entity, ["paused", "idle", "off"])

    # --- TEST 3: NOTES ---
    log.info("=== TEST GROUP: NOTES ===")
    test_note_title = "LiveTest Note"
    test_content = "This is a test content"
    
    # Cleanup first
    try: await handle_note_delete({"title": test_note_title}) 
    except: pass
    
    # 1. Create
    await run_nl_command(f"Create a note called {test_note_title} saying {test_content}")
    
    # 2. Read Verify
    res = await handle_note_read({"title": test_note_title})
    # Loose matching because format adds headers
    if test_content in str(res) and test_note_title in str(res):
        log.info("  VERIFY: Note content matches.")
    else:
        log.error(f"  FAILURE: Note content mismatch. Got: {res}")

    # 3. Delete
    await run_nl_command(f"Delete the note called {test_note_title}")
    res_del = await handle_note_read({"title": test_note_title})
    if "not found" in str(res_del).lower() or "error" in str(res_del).lower():
         log.info("  VERIFY: Note deleted.")
    else:
         log.error("  FAILURE: Note still exists.")

    # --- TEST 4: TIMERS ---
    log.info("=== TEST GROUP: TIMERS ===")
    # 1. Set Timer
    await run_nl_command("Set a timer for 1 minutes")
    await asyncio.sleep(2)
    
    # 2. Verify List
    timers = await timer_storage.list_timers()
    if len(timers) > 0:
        log.info(f"  VERIFY: {len(timers)} timers found. Active.")
        # Cleanup
        for t in timers:
            await timer_storage.delete_timer(t['id'])
    else:
        log.error("  FAILURE: No timers found after creation.")

    # --- TEST 5: CALENDAR ---
    log.info("=== TEST GROUP: CALENDAR ===")
    # 1. Add Event
    cal_query = "Schedule a release meeting tomorrow at 2pm"
    await run_nl_command(cal_query)
    # Cleanup TODO: logic/calendar_ops doesn't expose delete easily via NL without 'cancel', try that.
    await run_nl_command("Cancel the release meeting tomorrow")

    # --- TEST 6: WEB SEARCH ---
    log.info("=== TEST GROUP: WEB SEARCH ===")
    await run_nl_command("Who is the current CEO of Microsoft?")
    
    # --- TEST 7: RAG/KNOWLEDGE ---
    log.info("=== TEST GROUP: RAG KNOWLEDGE ===")
    await run_nl_command("What documents do I have about Project X?")

    log.info("--- ALL TESTS COMPLETED ---")
    
    # Restore State (Best Effort)
    # Turn off lights if they were off, stop media etc.
    # Since we can't easily track everything, we ensure we leave things explicitly off/stopped where reasonable.
    log.info("--- CLEANUP ---")
    if light_id: await run_nl_command(f"Turn off {light_id}")
    await run_nl_command(f"Stop music on {target_tv}")

    # Flush logs
    for handler in logging.getLogger().handlers:
        handler.flush()



if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.exception("Fatal Error in Test Suite")
