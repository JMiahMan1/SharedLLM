import sys
import subprocess
import os
import time
import json
import warnings
from datetime import datetime, timedelta
 
# --- 0. Dependency Auto-Heal ---
def check_and_install(package):
    try:
        __import__(package)
    except ImportError:
        print(f"[*] Dependency '{package}' missing. Installing...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
            print(f"[*] '{package}' installed successfully.")
        except Exception as e:
            print(f"[!] Failed to install '{package}'. Test cannot proceed. Error: {e}")
            sys.exit(1)
 
check_and_install("caldav")
check_and_install("requests")
check_and_install("python-dotenv")
 
import requests
import caldav
from dotenv import load_dotenv
from urllib3.exceptions import InsecureRequestWarning
warnings.simplefilter('ignore', InsecureRequestWarning)
 
# --- Setup ---
script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
env_path = os.path.join(root_dir, '.env')
load_dotenv(env_path)
 
API_URL = os.getenv("API_URL", "http://localhost:11435")
NC_URL = os.getenv("NEXTCLOUD_URL")
NC_USER = os.getenv("NEXTCLOUD_USER")
NC_PASS = os.getenv("NEXTCLOUD_PASS")

# FIX: Use the actual Nextcloud user for the API header so auth works
TEST_USER = NC_USER if NC_USER else "admin"
HEADERS = {"Content-Type": "application/json", "X-RAG-User": TEST_USER, "User-Agent": "TestScript"}
 
LAMP_NAME = "Piano Lamp"
MEDIA_NAME = "Office TV"
 
def print_header(title):
    print(f"\n{'='*60}\nTEST: {title}\n{'='*60}")
 
def find_entity_id(friendly_name):
    print(f"   [LOOKUP] Finding entity ID for '{friendly_name}'...")
    try:
        r = requests.get(f"{API_URL}/api/rag/search", params={"q": friendly_name, "k": 3}, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            results = r.json().get("results", [])
            for res in results:
                meta = res.get("metadata", {})
                if meta.get("source") == "home_assistant" and "entity_id" in meta:
                    eid = meta["entity_id"]
                    print(f"   [FOUND]  '{friendly_name}' -> {eid}")
                    return eid
    except Exception as e: print(f"   [ERROR] Lookup failed: {e}")
    return None
 
def get_state(entity_id):
    try:
        r = requests.get(f"{API_URL}/api/ha/state/{entity_id}", headers=HEADERS, timeout=5)
        if r.status_code == 200: return r.json().get("state")
    except: pass
    return "unknown"
 
def ensure_device_state(friendly_name, entity_id, target_state="off"):
    current = get_state(entity_id)
    # Smart media mapping: 'idle'/'paused' often means 'on' for TVs
    current_is_on = current in ["on", "idle", "playing", "paused", "buffering"]
   
    if target_state == "on" and current_is_on:
        print(f"   [PRE-CHECK] {friendly_name} is '{current}' (Counts as ON).")
        return True
    elif target_state == "off" and not current_is_on:
        print(f"   [PRE-CHECK] {friendly_name} is '{current}' (Counts as OFF).")
        return True
       
    print(f"   [PRE-CHECK] {friendly_name} is '{current}'. forcing '{target_state}'...")
   
    nl_cmd = f"Turn {target_state} the {friendly_name}"
    print(f"   [ACTION] Sending command: '{nl_cmd}'...")
    requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":nl_cmd}], "stream":False}, headers=HEADERS)
   
    for i in range(20):
        time.sleep(2)
        current = get_state(entity_id)
        current_is_on = current in ["on", "idle", "playing", "paused", "buffering"]
       
        if target_state == "on" and current_is_on:
            print(f"   [READY] {friendly_name} is now '{current}' (ON).")
            return True
        if target_state == "off" and not current_is_on:
            print(f"   [READY] {friendly_name} is now '{current}' (OFF).")
            return True
           
    print(f"   [WARNING] Failed to force {friendly_name} to {target_state}. Got '{current}'.")
    return False
 
def check_device_state(entity_id, expected_states, retries=5):
    if isinstance(expected_states, str): expected_states = [expected_states]
    print(f"   [VERIFYING] {entity_id} -> {expected_states}...")
    for i in range(retries):
        state = get_state(entity_id)
        if state in expected_states:
            print(f"   [PASS] {entity_id} is '{state}'.")
            return True
        if i < retries - 1: time.sleep(2.0)
    print(f"   [FAIL] {entity_id} state mismatch. Expected {expected_states}, got '{state}'")
    return False
 
def safe_post(url, payload, label):
    try:
        # Increased timeout for Calendar operations
        r = requests.post(url, json=payload, headers=HEADERS, timeout=180)
        try:
            resp_json = r.json()
            msg = resp_json.get("message", {}).get("content", "") or resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"   [RESPONSE] {label}: {msg.strip()[:120]}...")
            return msg
        except:
            print(f"   [RESPONSE] {label}: (Non-JSON response) {r.text[:50]}")
            return r.text
    except Exception as e:
        print(f"   [CRITICAL FAIL] {label} Timed out or Error: {e}")
        return None
 
# --- Tests ---
 
def test_history_context():
    print_header("Func: Shared Memory (Multi-Turn Context)")
    # Turn 1
    q1 = "Who is the president of France?"
    print(f"   [TURN 1] User: '{q1}'")
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":q1}], "stream":False}, "Turn 1")
   
    # Turn 2 (Ambiguous query that relies on history)
    q2 = "What is his wife's name?"
    print(f"   [TURN 2] User: '{q2}'")
    r2 = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":q2}], "stream":False}, "Turn 2")
   
    if r2 and ("macron" in r2.lower() or "brigitte" in r2.lower()):
        print("   [PASS] Context maintained across turns.")
    else:
        print("   [FAIL] Context lost. Response didn't seem to reference previous subject.")
 
def test_web_search_explicit():
    print_header("Func: Explicit Web Search")
    query = "Search the web for current Linux kernel version"
    print(f"   [QUERY] '{query}'")
   
    resp = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":query}], "stream":False}, "Web Search")
   
    resp_lower = resp.lower() if resp else ""
    if "cannot" in resp_lower or "unable" in resp_lower or "does not contain" in resp_lower:
         print("   [FAIL] API failed to retrieve search results.")
    elif "cutoff" in resp_lower or "2023" in resp_lower:
         print("   [FAIL] Response indicates hallucination/old data.")
    elif "linux" in resp_lower and ("kernel" in resp_lower or "stable" in resp_lower):
        print("   [PASS] Web Search returned relevant data.")
    else:
        print("   [FAIL] Response did not seem to contain search results.")
 
def test_calendar_integration():
    print_header("Func: Calendar (List -> Add -> Update -> Delete)")
    if not (NC_URL and NC_USER and NC_PASS):
        print("   [SKIP] Nextcloud credentials not set in .env")
        return
 
    test_event_title = f"RAG_Test_{int(time.time())}"
    
    # 1. Test List
    print(f"   [ACTION] Listing Calendars via API...")
    resp_list = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":"List my calendars"}], "stream":False}, "List Cals")
    if resp_list and "Available Calendars" in resp_list:
        print("   [PASS] Calendars listed.")
    else:
        print(f"   [WARN] Calendar list check failed. Response: {str(resp_list)[:100]}...")

    # 2. Test Add
    print(f"   [ACTION] Adding Event: '{test_event_title}'...")
    create_cmd = f"Schedule a {test_event_title} tomorrow at 10am"
    resp_add = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":create_cmd}], "stream":False}, "Cal Add")
    
    if resp_add and "Scheduled" in resp_add:
        print("   [PASS] Add confirmed by API.")
    else:
        print(f"   [FAIL] API did not confirm schedule. Response: {resp_add}")

    time.sleep(2) 

    # 3. Test Update
    print(f"   [ACTION] Rescheduling Event: '{test_event_title}'...")
    update_cmd = f"Reschedule the event {test_event_title} to tomorrow at 2pm"
    resp_upd = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":update_cmd}], "stream":False}, "Cal Update")
    
    if resp_upd and "Rescheduled" in resp_upd:
        print("   [PASS] Update confirmed by API.")
    else:
        print(f"   [FAIL] API did not confirm update. Response: {resp_upd}")

    time.sleep(2)

    # 4. Test Delete
    print(f"   [ACTION] Deleting Event: '{test_event_title}'...")
    del_cmd = f"Cancel the event {test_event_title}"
    resp_del = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":del_cmd}], "stream":False}, "Cal Delete")
    
    if resp_del and "Deleted" in resp_del:
        print("   [PASS] Delete confirmed by API.")
    else:
        print(f"   [FAIL] Delete failed or ambiguous. Response: {resp_del}")

    # 5. Safety Cleanup (Direct CalDAV)
    try:
        cal_url = f"{NC_URL.rstrip('/')}/remote.php/dav"
        client = caldav.DAVClient(url=cal_url, username=NC_USER, password=NC_PASS)
        calendars = client.principal().calendars()
        if calendars:
            start = datetime.now()
            end = start + timedelta(days=14)
            # FIX: Use search instead of deprecated date_search
            # We also filter writable calendars here to ensure we check the same ones the API did
            candidates = [c for c in calendars if "contact" not in (c.name or "").lower() and "birthday" not in (c.name or "").lower()]
            
            for c in candidates:
                try:
                    events = c.search(start=start, end=end, event=True, expand=True)
                    for ev in events:
                        if hasattr(ev.vobject_instance, 'vevent'):
                            if ev.vobject_instance.vevent.summary.value == test_event_title:
                                ev.delete()
                                print("   [CLEANUP] Removed leftover test event directly.")
                except: pass
    except Exception as e:
        print(f"   [INFO] Cleanup skipped or failed: {e}")

def test_natural_language_date():
    print_header("Func: Calendar (Natural Language Date)")
    if not (NC_URL and NC_USER and NC_PASS):
        print("   [SKIP] Nextcloud credentials not set.")
        return

    # Test: "Schedule [Event] next Friday at noon"
    evt_name = f"NL_Test_{int(time.time())}"
    cmd = f"Schedule {evt_name} next Friday at noon"
    print(f"   [SENDING] '{cmd}'")
    resp = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":cmd}], "stream":False}, "NL Schedule")

    if resp and "Scheduled" in resp and evt_name in resp:
        print("   [PASS] Natural language scheduling succeeded.")
        # Cleanup via API
        time.sleep(1)
        safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":f"Delete {evt_name}"}], "stream":False}, "Cleanup")
    else:
        print(f"   [FAIL] Natural language scheduling failed. Response: {resp}")

def test_cross_domain_multi_command(lamp_id, lamp_name):
    print_header("Func: Multi-Intent (Cross-Domain: HA + Calendar)")
    if not (NC_URL and NC_USER and NC_PASS):
        print("   [SKIP] Credentials missing.")
        return

    # Setup: Lamp OFF
    ensure_device_state(lamp_name, lamp_id, "off")

    # Command: "Turn on [Lamp] and List my Calendars"
    cmd = f"Turn on the {lamp_name} and List my Calendars"
    print(f"   [SENDING] '{cmd}'")
    resp = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":cmd}], "stream":False}, "Cross-Domain Cmd")

    # Verify Lamp
    is_on = check_device_state(lamp_id, "on", retries=5)

    # Verify Calendar List in text
    has_cal = resp and "Available Calendars" in resp

    if is_on and has_cal:
        print("   [PASS] Both actions (HA + Calendar) executed successfully.")
    elif is_on:
        print("   [FAIL] Lamp turned on, but Calendar list missing in response.")
    elif has_cal:
        print("   [FAIL] Calendar listed, but Lamp did not turn on.")
    else:
        print("   [FAIL] Both actions failed.")
 
def test_music_playback(entity_id, friendly_name):
    print_header(f"Func: Music Assistant (Play -> Stop on {friendly_name})")
   
    if not ensure_device_state(friendly_name, entity_id, "on"):
        print("   [SKIP] Device not ready for music test.")
        return
 
    cmd = f"Play Brandon Lake on the {friendly_name}"
    print(f"   [SENDING] '{cmd}'")
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":cmd}], "stream":False}, "Music Play")
   
    print(f"   [VERIFYING] {entity_id} -> 'playing'...")
    playing = False
    for i in range(10):
        state = get_state(entity_id)
        if state in ["playing", "buffering"]:
            print(f"   [PASS] {entity_id} is '{state}'.")
            playing = True
            break
        time.sleep(2)
   
    if not playing:
        print(f"   [FAIL] Music failed to start. Current State: {get_state(entity_id)}")
    else:
        time.sleep(3)
 
    print(f"   [ACTION] Stopping music...")
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":f"Stop the {friendly_name}"}], "stream":False}, "Music Stop")
    
    # FIXED: Added 'on' to acceptable stop states because many TVs stay on (idle) after stopping
    check_device_state(entity_id, ["paused", "idle", "off", "on"])

def test_notes_integration():
    print_header("Func: Notes System (Create -> Read -> Append)")
    
    timestamp = int(time.time())
    note_title = f"LiveTest_{timestamp}"
    content_body = "This is a live test note content."
    
    # 1. CREATE
    print(f"   [ACTION] Creating Note '{note_title}'...")
    create_cmd = f"Create a note called {note_title} that says {content_body}"
    resp = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":create_cmd}], "stream":False}, "Note Create")
    
    if resp and ("success" in resp.lower() or "created" in resp.lower() or "saved" in resp.lower()):
        print(f"   [PASS] Note creation confirmed.")
    else:
        print(f"   [FAIL] Note creation failed. Response: {resp}")

    time.sleep(1)

    # 2. READ
    print(f"   [ACTION] Reading Note '{note_title}'...")
    read_cmd = f"Read the note {note_title}"
    resp = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":read_cmd}], "stream":False}, "Note Read")
    
    if resp and content_body in resp:
        print(f"   [PASS] Note content verified.")
    else:
        print(f"   [FAIL] Content mismatch or read failure. Response: {resp}")

    # 3. APPEND
    print(f"   [ACTION] Appending to Note '{note_title}'...")
    append_cmd = f"Add 'Buy Milk' to my {note_title} note"
    resp = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":append_cmd}], "stream":False}, "Note Append")
    
    if resp and ("success" in resp.lower() or "added" in resp.lower()):
        print(f"   [PASS] Note append confirmed.")
    else:
        print(f"   [FAIL] Note append failed. Response: {resp}")
    
    # 4. READ AGAIN
    print(f"   [ACTION] Verifying Append...")
    resp = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":read_cmd}], "stream":False}, "Note Verify")
    
    if resp and "Buy Milk" in resp:
        print("   [PASS] Append verified in content.")
    else:
        print(f"   [FAIL] Appended content not found.")

 
def test_functionality():
    print_header("Protocol: Health & Streaming")
    if requests.get(f"{API_URL}/health").status_code == 200: print("   [PASS] Health Check OK")
   
    print_header("Func: Entity Discovery")
    lamp_id = find_entity_id(LAMP_NAME)
    tv_id = find_entity_id(MEDIA_NAME)
 
    if not lamp_id or not tv_id:
        print("   [CRITICAL FAIL] Entities not found in RAG. Aborting.")
        return
 
    ensure_device_state(LAMP_NAME, lamp_id, "off")
 
    test_history_context() 
    test_web_search_explicit() 
    test_calendar_integration()
    test_notes_integration()
    test_natural_language_date()
    test_cross_domain_multi_command(lamp_id, LAMP_NAME)
 
    print_header(f"Func: Control (Turn On {LAMP_NAME})")
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":f"Turn on the {LAMP_NAME}"}], "stream":False}, "Turn On")
    check_device_state(lamp_id, "on")
 
    test_music_playback(tv_id, MEDIA_NAME)
 
    print_header("Func: Multi-Command (Turn OFF Both)")
    print("   [SETUP] Forcing BOTH devices ON...")
    lamp_ready = ensure_device_state(LAMP_NAME, lamp_id, "on")
    tv_ready = ensure_device_state(MEDIA_NAME, tv_id, "on")
   
    if not tv_ready:
        print("   [SKIP] TV failed to turn ON. Skipping OFF test.")
        return
 
    cmd = f"Turn off the {LAMP_NAME} and the {MEDIA_NAME}"
    print(f"   [SENDING] '{cmd}'")
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":cmd}], "stream":False}, "Multi-Cmd")
   
    check_device_state(lamp_id, "off")
    check_device_state(tv_id, ["off", "idle", "standby", "unavailable"])
 
def main():
    print(f"Starting Robust Tests on {API_URL}...\n")
    try:
        if requests.get(f"{API_URL}/health", timeout=5).status_code != 200:
            print("API Down"); return
    except: print("API Unreachable"); return
 
    test_functionality()
    print("\nTEST SEQUENCE COMPLETE")
 
if __name__ == "__main__":
    main()
