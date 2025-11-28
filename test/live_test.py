import sys
import subprocess
import os
import time
import json
import warnings
import concurrent.futures
from datetime import datetime, timedelta
 
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

def test_ingestion_trigger():
    print_header("Func: Ingestion Triggers (Smoke Test)")
    try:
        # Increased timeout for safety
        r = requests.post(f"{API_URL}/ingest/ha", timeout=20)
        if r.status_code == 200:
            print("   [PASS] HA Ingestion triggered successfully.")
            # Wait for DB update
            time.sleep(5)
        else:
            print(f"   [FAIL] HA Ingestion trigger failed: {r.status_code}")
    except Exception as e:
        print(f"   [WARN] Ingestion trigger error: {e}")

def test_calendar_stress():
    print_header("Func: Calendar Stress Test (Cache Validation)")
    print("   [INFO] sending 5 parallel 'List Calendars' requests to verify cache hits...")
    
    def _req(i):
        start = time.time()
        requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":"List my calendars"}]}, headers=HEADERS)
        return time.time() - start

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_req, i) for i in range(5)]
        results = [f.result() for f in futures]
    
    avg = sum(results) / len(results)
    print(f"   [STATS] Avg Response Time: {avg:.2f}s")
    if avg < 5.0:
        print("   [PASS] Cache likely effective (Avg < 5s).")
    else:
        print("   [WARN] Response time slow. Cache might be missing.")

def test_calendar_integration():
    print_header("Func: Calendar (List -> Add -> Update -> Delete)")
    if not (NC_URL and NC_USER and NC_PASS):
        print("   [SKIP] Nextcloud credentials not set in .env")
        return
 
    test_event_title = f"RAG_Test_{int(time.time())}"
    
    print(f"   [ACTION] Listing Calendars via API...")
    resp_list = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":"List my calendars"}], "stream":False}, "List Cals")
    if resp_list and "Available Calendars" in resp_list:
        print("   [PASS] Calendars listed.")
    else:
        print(f"   [WARN] Calendar list check failed. Response: {str(resp_list)[:100]}...")

    print(f"   [ACTION] Adding Event: '{test_event_title}'...")
    create_cmd = f"Schedule a {test_event_title} tomorrow at 10am on Jeremiah"
    resp_add = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":create_cmd}], "stream":False}, "Cal Add")
    
    if resp_add and "Scheduled" in resp_add:
        print("   [PASS] Add confirmed by API.")
    else:
        print(f"   [FAIL] API did not confirm schedule. Response: {resp_add}")

    time.sleep(2) 

    print(f"   [ACTION] Rescheduling Event: '{test_event_title}'...")
    update_cmd = f"Reschedule the event {test_event_title} to tomorrow at 2pm"
    resp_upd = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":update_cmd}], "stream":False}, "Cal Update")
    
    if resp_upd and "Rescheduled" in resp_upd:
        print("   [PASS] Update confirmed by API.")
    else:
        print(f"   [FAIL] API did not confirm update. Response: {resp_upd}")

    time.sleep(2)

    print(f"   [ACTION] Deleting Event: '{test_event_title}'...")
    del_cmd = f"Cancel the event {test_event_title}"
    resp_del = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":del_cmd}], "stream":False}, "Cal Delete")
    
    if resp_del and "Deleted" in resp_del:
        print("   [PASS] Delete confirmed by API.")
    else:
        print(f"   [FAIL] Delete failed or ambiguous. Response: {resp_del}")

    try:
        cal_url = f"{NC_URL.rstrip('/')}/remote.php/dav"
        client = caldav.DAVClient(url=cal_url, username=NC_USER, password=NC_PASS)
        calendars = client.principal().calendars()
        if calendars:
            start = datetime.now()
            end = start + timedelta(days=14)
            for c in calendars:
                try:
                    events = c.search(start=start, end=end, event=True, expand=True)
                    for ev in events:
                        if hasattr(ev.vobject_instance, 'vevent'):
                            if ev.vobject_instance.vevent.summary.value == test_event_title:
                                ev.delete()
                                print(f"   [CLEANUP] Removed leftover test event from {c.name}.")
                except: pass
    except Exception as e:
        print(f"   [INFO] Cleanup skipped or failed: {e}")
 
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
        # Relaxed check for devices that don't report 'playing' reliably
        if state in ["playing", "buffering", "on"]:
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
    check_device_state(entity_id, ["paused", "idle", "off"])
 
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
 
    test_ingestion_trigger()
    test_calendar_stress()
    test_calendar_integration()
 
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
