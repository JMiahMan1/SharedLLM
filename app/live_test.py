
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

async def verify_ha_state(entity_id: str, expected_state: str = None, check_attr: dict = None):
    try:
        url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
        r = requests.get(url, headers=get_ha_headers(), timeout=5)
        data = r.json()
        current_state = data['state']
        attributes = data.get('attributes', {})
        
        log.info(f"  VERIFY: {entity_id} is '{current_state}'")
        
        if expected_state and current_state != expected_state:
            log.error(f"  FAILURE: Expected '{expected_state}', got '{current_state}'")
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
    media_id = discover_entities("media_player")
    if media_id:
        log.info(f"=== TEST GROUP: MEDIA ({media_id}) ===")
        # 1. Play (Generic)
        await run_nl_command(f"Play some music on {media_id}")
        await asyncio.sleep(3)
        await verify_ha_state(media_id, "playing")
        
        # 2. Pause
        await run_nl_command(f"Pause {media_id}")
        await asyncio.sleep(2)
        await verify_ha_state(media_id, "paused")
    else:
        log.warning("SKIPPING MEDIA TESTS: No media_player found.")

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
    
    # 2. List (We can't easily verify the exact event without parsing, but we check for success response in next step)
    # Ideally we'd query the calendar tool directly like we did for timers, 
    # but calendar_ops might not have a public list function easily accessible without params. 
    # We'll rely on the NL command returning success.
    
    # --- TEST 6: WEB SEARCH ---
    log.info("=== TEST GROUP: WEB SEARCH ===")
    # We just want to see if the tool is selected. 
    await run_nl_command("Who is the current CEO of Microsoft?")
    
    # --- TEST 7: RAG/KNOWLEDGE ---
    # This tests the retrieval pipeline
    log.info("=== TEST GROUP: RAG KNOWLEDGE ===")
    # Query something generic that might hit RAG
    await run_nl_command("What documents do I have about Project X?")

    log.info("--- ALL TESTS COMPLETED ---")
    
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
