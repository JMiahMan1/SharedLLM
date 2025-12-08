
import sys
import os
import time
import requests
import json
import logging
from datetime import datetime

# --- Configuration ---
API_URL = os.getenv("API_URL", "http://192.168.2.211:11435")
HEADERS = {"Content-Type": "application/json", "X-RAG-User": "admin_test_suite"}
LOG_FILE = "live_test_suite_results.txt"

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', handlers=[
    logging.StreamHandler(sys.stdout),
    logging.FileHandler(LOG_FILE)
])
log = logging.getLogger("LiveTest")

def send_query(query, history=None):
    log.info(f"USER: {query}")
    payload = {"messages": history if history else [{"role":"user","content":query}], "stream":False}
    if history:
        payload["messages"].append({"role":"user", "content":query})
    
    try:
        r = requests.post(f"{API_URL}/api/chat", json=payload, headers=HEADERS, timeout=60)
        if r.status_code != 200:
            log.error(f"HTTP {r.status_code}: {r.text}")
            return None, history
        
        data = r.json()
        msg = data.get("message", {}).get("content", "")
        log.info(f"AI: {msg}")
        
        # Update history
        if history is None:
            history = [{"role":"user", "content":query}]
        else:
            history.append({"role":"user", "content":query})
        history.append({"role":"assistant", "content":msg})
        
        return msg, history
    except Exception as e:
        log.error(f"EXCEPTION: {e}")
        return None, history

def verify(condition, success_msg, fail_msg):
    if condition:
        log.info(f"[PASS] {success_msg}")
        return True
    else:
        log.error(f"[FAIL] {fail_msg}")
        return False

# --- Test Suites ---

# --- State Management ---
STATE_BACKUP = {}

def get_entity_state(entity_id):
    try:
        r = requests.get(f"{API_URL}/api/ha/state/{entity_id}", headers=HEADERS, timeout=5)
        if r.status_code == 200:
            data = r.json()
            return data.get("state"), data.get("attributes", {})
    except Exception as e:
        log.warning(f"Failed to get state for {entity_id}: {e}")
    return "unknown", {}

def backup_state(entity_id):
    state, attrs = get_entity_state(entity_id)
    STATE_BACKUP[entity_id] = {"state": state, "attributes": attrs}
    log.info(f"[BACKUP] Saved {entity_id}: {state}")

def restore_state(entity_id):
    if entity_id not in STATE_BACKUP: return
    saved = STATE_BACKUP[entity_id]
    current, _ = get_entity_state(entity_id)
    
    if saved["state"] == "off" and current != "off":
        log.info(f"[RESTORE] Turning off {entity_id}")
        # Use LLM command for now as we don't have direct HA service API in this script
        # Alternatively, exposes /api/ha/service? No.
        # We rely on the agent to do it.
        send_query(f"Turn off {entity_id}")
        
    elif saved["state"] == "on":
        log.info(f"[RESTORE] Restoring {entity_id} to ON")
        # Checking color/brightness is hard via chat, but we can try basic restore
        if "brightness" in saved["attributes"]:
             send_query(f"Turn on {entity_id}") # Basic on
        else:
             send_query(f"Turn on {entity_id}")

# --- Test Suites ---

def test_context_chain():
    log.info("\n=== TEST: Context Chain (President) ===")
    history = []
    
    # Q1
    resp, history = send_query("Who is the president of France?", history)
    if not verify("Macron" in resp, "Identified Macron", "Failed to identify Macron"): return

    # Q2 (Contextual)
    resp, history = send_query("Who is his wife?", history)
    verify("Brigitte" in resp, "Identified Brigitte (Context Maintained)", "Failed to resolve 'his wife' context")

def test_media_lifecycle():
    log.info("\n=== TEST: Media Lifecycle & Cleanup ===")
    entity = "media_player.office_tv_chrome" # Explicit ID or mapped name
    
    backup_state(entity)
    
    # 1. Turn On
    send_query("Turn on the Office TV")
    time.sleep(3)
    
    # 2. Play Music (Test Mass Swap)
    resp, _ = send_query("Play Brandon Lake on the Office TV")
    # Accept empty string or SILENT_SUCCESS token or "Playing"
    verify(resp == "" or "[SILENT_SUCCESS]" in resp or "Playing" in resp, "Music playback started", "Music playback failed")
    time.sleep(5)
    
    # 3. Skip (Test Context Persistence)
    resp, _ = send_query("Skip this song")
    # Accept empty string or SILENT_SUCCESS token
    verify(resp == "" or "[SILENT_SUCCESS]" in resp, "Context skipped song successfully", "Context failed (asked for device)")
    time.sleep(3)
    
    # 4. Stop
    send_query("Stop on Office TV")
    
    # 5. Restore
    restore_state(entity)

def test_color_restore():
    log.info("\n=== TEST: Color Restoration ===")
    entity = "light.kitchen_light_1" # Adjust based on your setup
    
    backup_state(entity)
    
    # 1. Ensure On
    send_query("Turn on Kitchen Light 1")
    time.sleep(2)

    # 2. Turn Blue
    send_query("Turn Kitchen Light 1 Blue")
    time.sleep(3)
    
    # 2. Turn to Warm White (Checking color command)
    send_query("Turn Kitchen Light 1 to Warm White")
    time.sleep(2)
    
    # 3. Restore
    restore_state(entity)

def test_tools_cleanup():
    log.info("\n=== TEST: Tools (Note/Timer/Calendar) & Cleanup ===")
    ts = int(time.time())
    
    # NOTE
    note_title = f"TestNote_{ts}"
    send_query(f"Create a note called {note_title} saying 'Verification Test'")
    resp, _ = send_query(f"Read my {note_title} note")
    verify("Verification Test" in resp, "Note created and read", "Note read failed")
    send_query(f"Delete note {note_title}")
    log.info(f"[CLEANUP] Note {note_title} deleted.")
    
    # TIMER
    send_query("Set a timer for 5 seconds on Office TV")
    log.info("Waiting 7s for timer to expire...")
    time.sleep(7)
    
    # CALENDAR
    event_title = f"TestEvent_{ts}"
    send_query(f"Schedule {event_title} tomorrow at 9am")
    send_query(f"Cancel the meeting {event_title}")
    log.info(f"[CLEANUP] Event {event_title} cancelled.")

if __name__ == "__main__":
    test_context_chain()
    test_media_lifecycle()
    test_color_restore()
    test_tools_cleanup()
