import requests
import json
import time
import sys
import os
import argparse
from typing import Dict, Any, Optional

# Constants
API_URL = os.getenv("SERVER_URL", "http://192.168.2.211:11435")
HA_URL = os.getenv("HA_URL") # Must be provided or read from env if available
HA_TOKEN = os.getenv("HA_TOKEN") 
DEVICE_NAME = "TCL Roku TV" # Default target
ENTITY_ID = "media_player.28_tcl_roku_tv" # Target entity for state verification

# Setup Logging
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("test_chat_lifecycle")

def chat_query(query: str, history: list = []) -> Dict[str, Any]:
    """Send a query to the Chat API."""
    url = f"{API_URL}/api/chat"
    payload = {
        "query": query,
        "history": history,
        "user_id": "test_user"
    }
    
    log.info(f"User: {query}")
    try:
        start_time = time.time()
        resp = requests.post(url, json=payload, timeout=120) # Long timeout for download/cast
        duration = time.time() - start_time
        resp.raise_for_status()
        data = resp.json()
        log.info(f"Assistant: {data.get('response')} (took {duration:.1f}s)")
        return data
    except Exception as e:
        log.error(f"Chat Request Failed: {e}")
        return {}

def get_ha_state() -> Optional[str]:
    """Get current state of the entity from HA."""
    if not HA_URL or not HA_TOKEN:
        log.warning("HA_URL or HA_TOKEN not set. Cannot verify state.")
        return None
        
    url = f"{HA_URL}/api/states/{ENTITY_ID}"
    headers = {"Authorization": f"Bearer {HA_TOKEN}"}
    
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        state = data.get("state")
        attributes = data.get("attributes", {})
        log.info(f"[State Check] {ENTITY_ID}: {state} (App: {attributes.get('app_name', 'N/A')})")
        return state
    except Exception as e:
        log.error(f"HA State Check Failed: {e}")
        return "unknown"

def wait_for_state(target_states: list, timeout: int = 30) -> bool:
    """Wait for entity to reach one of the target states."""
    log.info(f"Waiting for state in {target_states}...")
    start = time.time()
    while time.time() - start < timeout:
        state = get_ha_state()
        if state in target_states:
            return True
        time.sleep(2)
    log.error(f"Timeout waiting for state {target_states}")
    return False

def test_lifecycle():
    log.info("=== Starting End-to-End Lifecycle Test ===")
    
    # 0. Initial State
    initial_state = get_ha_state()
    
    # 1. Turn On if Off
    if initial_state == "off":
        chat_query(f"Turn on the {DEVICE_NAME}")
        if wait_for_state(["idle", "home", "on"], timeout=20):
             log.info("-> Detected ON state.")
        else:
             log.warning("-> Failed to detect ON state (might be slow).")

    # 2. Watch Video (Triggers Download + Cast)
    # Using a known safe query that should find a video quickly
    chat_query(f"Watch Brandon Lake on the {DEVICE_NAME}")
    
    # Wait for 'playing' or 'buffering'
    # Download might take time, so give it generous timeout
    if wait_for_state(["playing", "buffering"], timeout=60):
        log.info("SUCCESS: Playback started.")
    else:
        log.error("FAILURE: Playback did not start within timeout.")
        return False
        
    # Let it play for a bit
    time.sleep(10)
    
    # 3. Pause
    chat_query(f"Pause the {DEVICE_NAME}")
    if wait_for_state(["paused"], timeout=15):
        log.info("SUCCESS: Paused.")
    else:
        log.warning("FAILURE: Could not pause (or state not updated).")
        
    time.sleep(3)
    
    # 4. Resume
    chat_query(f"Resume the {DEVICE_NAME}")
    if wait_for_state(["playing", "buffering"], timeout=15):
         log.info("SUCCESS: Resumed.")
    else:
         log.warning("FAILURE: Could not resume.")
         
    time.sleep(5)
    
    # 5. Stop
    chat_query(f"Stop the {DEVICE_NAME}")
    if wait_for_state(["idle", "home", "on"], timeout=15):
        log.info("SUCCESS: Stopped.")
    else:
        log.warning("FAILURE: Could not stop.")
        
    # 6. Turn Off
    chat_query(f"Turn off the {DEVICE_NAME}")
    if wait_for_state(["off", "standby"], timeout=20):
        log.info("SUCCESS: Turned Off.")
    else:
        log.warning("FAILURE: Could not turn off.")
        
    log.info("=== Test Complete ===")
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ha-url", default=os.getenv("HA_URL"), help="Home Assistant URL")
    parser.add_argument("--ha-token", default=os.getenv("HA_TOKEN"), help="Home Assistant Token")
    args = parser.parse_args()
    
    if args.ha_url: HA_URL = args.ha_url
    if args.ha_token: HA_TOKEN = args.ha_token
    
    if not HA_URL or not HA_TOKEN:
        log.error("Missing HA_URL or HA_TOKEN. Please set env vars or pass args.")
        sys.exit(1)
        
    try:
        test_lifecycle()
    except KeyboardInterrupt:
        log.info("Test aborted by user.")
