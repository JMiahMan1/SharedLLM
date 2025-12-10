
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
console = logging.StreamHandler()
logging.getLogger().addHandler(console)
log = logging.getLogger("LiveTest")

from settings import load_resources, GlobalResources, HA_URL, get_user_creds, DEFAULT_MODEL
from logic import contextualize_query, try_handle_compound_command

def get_ha_headers():
    creds = get_user_creds("default")
    return {
        "Authorization": f"Bearer {creds['ha_token']}",
        "Content-Type": "application/json"
    }

async def setup():
    log.info("--- SETUP: Loading Resources ---")
    await load_resources()
    if notRd GlobalResources.redis_client or not GlobalResources.chroma_client:
        log.error("CRITICAL: Resources failed to load.")
        sys.exit(1)

async def run_nl_command(query: str):
    log.info(f"TEST ACTION: '{query}'")
    user = "admin"
    creds = get_user_creds(user)
    
    refined, intent, score, is_high_confidence = await contextualize_query(query, user, DEFAULT_MODEL)
    log.info(f"  -> Refined: '{refined}', Intent: {intent} (Score: {score})")
    
    result = await try_handle_compound_command(refined, creds, DEFAULT_MODEL, intent, score, is_high_confidence)
    log.info(f"  -> Result: {result}")
    return result

async def verify_ha_state(entity_id: str, expected_state, timeout=5):
    """Verifies HA state with simple polling"""
    if not isinstance(expected_state, list):
        expected_state = [expected_state]
        
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
            r = requests.get(url, headers=get_ha_headers(), timeout=2)
            if r.status_code == 200:
                curr = r.json().get('state')
                if curr in expected_state:
                    log.info(f"  VERIFY: {entity_id} is '{curr}' (Expected)")
                    return True
        except: pass
        await asyncio.sleep(1)
        
    # Final check / log failure
    try:
        url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
        r = requests.get(url, headers=get_ha_headers(), timeout=2)
        curr = r.json().get('state')
        log.error(f"  FAILURE: Expected {expected_state}, got '{curr}'")
    except Exception as e:
        log.error(f"  FAILURE: Could not fetch state for {entity_id}: {e}")
    return False

async def get_all_ma_players():
    """Discover all Music Assistant players dynamically."""
    ma_targets = []
    try:
        req = requests.get(f"{HA_URL}/api/states", headers=get_ha_headers())
        if req.status_code == 200:
            for s in req.json():
                eid = s['entity_id']
                if not eid.startswith("media_player."): continue
                
                attrs = s.get("attributes", {})
                is_ma = attrs.get("app_id") == "music_assistant" or "mass_player_type" in attrs or "music_assistant" in s.get("attributes", {}).get("integration", "")
                
                if is_ma:
                    fname = attrs.get("friendly_name", eid)
                    ma_targets.append({"name": fname, "entity_id": eid, "state": s['state']})
    except Exception as e:
        log.error(f"Discovery failed: {e}")
    return ma_targets

async def cleanup_all_media():
    """Force stop on all active media players."""
    log.info("\n=== FINAL CLEANUP: STOPPING ALL MEDIA ===")
    try:
        req = requests.get(f"{HA_URL}/api/states", headers=get_ha_headers())
        active_count = 0
        if req.status_code == 200:
            for s in req.json():
                eid = s['entity_id']
                if eid.startswith("media_player.") and s['state'] in ['playing', 'buffering', 'paused']:
                    log.info(f"Cleanup: Stopping {eid} (State: {s['state']})...")
                    # Use NL command to test our logic even in cleanup
                    # But simpler: use direct service call if needed. Let's use NL to verify logic.
                    # Use explicit entity ID in query to avoid ambiguity
                    await run_nl_command(f"Stop media on {eid}")
                    active_count += 1
        
        if active_count == 0:
            log.info("Cleanup: All devices appear idle.")
        else:
            await asyncio.sleep(2)
            # Verify
            req = requests.get(f"{HA_URL}/api/states", headers=get_ha_headers())
            for s in req.json():
                if s['entity_id'].startswith("media_player.") and s['state'] in ['playing', 'buffering']:
                     log.warning(f"Cleanup Warning: {s['entity_id']} is still {s['state']}!")

    except Exception as e:
        log.error(f"Cleanup failed: {e}")

async def main():
    await setup()
    
    # --- DYNAMIC DISCOVERY & TESTING ---
    log.info("\n=== DYNAMIC DISCOVERY: Scanning for Music Assistant Players ===")
    ma_targets = await get_all_ma_players()
    log.info(f"Discovered {len(ma_targets)} Music Assistant Players: {json.dumps(ma_targets, indent=2)}")

    for target in ma_targets:
        fname = target['name']
        entity_id = target['entity_id']
        
        # SKIP TESTING if unavailable
        if target.get('state') == 'unavailable':
             log.warning(f"Skipping unavailable device: {fname}")
             continue

        log.info(f"\n--- TESTING TARGET: '{fname}' (ID: {entity_id}) ---")
        
        # Test 1: Play
        query = f"Play Brandon Lake on {fname}"
        await run_nl_command(query)
        log.info(f"  VERIFYING: {entity_id} should be PLAYING")
        await verify_ha_state(entity_id, ["playing", "buffering"], timeout=10)
        
        # Test 2: Stop
        query = f"Stop music on {fname}"
        await run_nl_command(query)
        log.info(f"  VERIFYING: {entity_id} should be IDLE/PAUSED")
        await verify_ha_state(entity_id, ["idle", "paused", "off"], timeout=5)

    # --- FINAL CLEANUP ---
    await cleanup_all_media()
    
    log.info("--- ALL TESTS COMPLETED ---")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.exception("Fatal Error in Test Suite")
