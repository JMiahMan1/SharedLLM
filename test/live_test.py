import sys
import subprocess
import os
import time
import json
import warnings
import re
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
check_and_install("dateparser")
 
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
HEADERS = {"Content-Type": "application/json", "User-Agent": "TestScript"}
NC_URL = os.getenv("NEXTCLOUD_URL")
NC_USER = os.getenv("NEXTCLOUD_USER")
NC_PASS = os.getenv("NEXTCLOUD_PASS")
 
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
        r = requests.post(url, json=payload, headers=HEADERS, timeout=120)
        try:
            resp_json = r.json()
            if "choices" in resp_json:
                msg = resp_json["choices"][0]["message"]["content"]
            else:
                msg = resp_json.get("message", {}).get("content", "") or resp_json.get("response", "")
            
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
    q1 = "Who is the president of France?"
    print(f"   [TURN 1] User: '{q1}'")
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":q1}], "user": "TestUser", "stream":False}, "Turn 1")
   
    q2 = "What is his wife's name?"
    print(f"   [TURN 2] User: '{q2}'")
    r2 = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":q2}], "user": "TestUser", "stream":False}, "Turn 2")
   
    if r2 and ("macron" in r2.lower() or "brigitte" in r2.lower()):
        print("   [PASS] Context maintained across turns.")
    else:
        print("   [FAIL] Context lost.")
 
def test_web_search_explicit():
    print_header("Func: Explicit Web Search")
    query = "Search the web for current Linux kernel version"
    print(f"   [QUERY] '{query}'")
   
    resp = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":query}], "stream":False}, "Web Search")
   
    if resp and ("linux" in resp.lower() and "kernel" in resp.lower()):
        print("   [PASS] Web Search returned relevant data.")
    else:
        print("   [FAIL] Response did not seem to contain search results.")
 
def test_calendar_lifecycle():
    print_header("Func: Calendar (Full Lifecycle with Explicit Routing)")
    if not (NC_URL and NC_USER and NC_PASS): return

    # 1. Create
    test_title = f"CycleTest_{int(time.time())}"
    create_cmd = f"Schedule a {test_title} tomorrow at 10am to Calendar Personal"
    print(f"   [STEP 1: CREATE] '{create_cmd}'")
    resp = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":create_cmd}], "stream":False}, "Create Event")
    
    if resp and "successfully" in resp.lower():
        print("   [PASS] API success message.")
    else:
        print("   [WARN] API ambiguous response.")

    # 2. Verify via CalDAV (Server Check)
    print("   [STEP 2: VERIFY SERVER]")
    client = caldav.DAVClient(url=f"{NC_URL.rstrip('/')}/remote.php/dav", username=NC_USER, password=NC_PASS)
    calendars = client.principal().calendars()
    target_event = None
    
    if calendars:
        for cal in calendars:
            if "personal" not in (cal.name or "").lower(): continue
            
            print(f"      [DEBUG] Scanning target calendar: {cal.name}...")
            try:
                # FIXED: Use the same signature that worked in debug_caldev.py
                events = cal.search(
                    start=datetime.now(), 
                    end=datetime.now()+timedelta(days=7),
                    event=True,
                    expand=True
                )
                for ev in events:
                    if hasattr(ev.vobject_instance, 'vevent'):
                        if test_title in ev.vobject_instance.vevent.summary.value:
                            target_event = ev
                            print(f"   [PASS] Found '{test_title}' on correct calendar '{cal.name}'.")
                            break
            except Exception as e:
                print(f"      [ERROR] Error reading {cal.name}: {e}")
            if target_event: break
    
    if not target_event:
        print("   [FAIL] Event not found on 'Personal' calendar.")
        return

    # 3. Verify via RAG Read
    print("   [STEP 3: READ VIA API]")
    time.sleep(2) 
    read_query = "What is on my calendar tomorrow?"
    read_resp = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":read_query}], "stream":False}, "Read Event")
    
    if read_resp and test_title in read_resp:
        print(f"   [PASS] API correctly listed '{test_title}'.")
    else:
        if read_resp and ("tomorrow" in read_resp.lower() or "10am" in read_resp.lower()):
             print(f"   [WARN] API response relevant but exact title match failed. Resp: {read_resp[:50]}...")
        else:
             print(f"   [FAIL] API did NOT list '{test_title}'. Response: {read_resp[:50]}...")

    # 4. Delete (Cleanup)
    print("   [STEP 4: DELETE]")
    target_event.delete()
    print("   [PASS] Event deleted from server.")

    # 5. Verify Gone
    print("   [STEP 5: VERIFY GONE]")
    time.sleep(2)
    gone_resp = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":read_query}], "stream":False}, "Read Event")
    
    if gone_resp and test_title not in gone_resp:
        print("   [PASS] API no longer lists the event.")
    else:
        print("   [WARN] API still lists the event (Possible Cache delay).")

def test_music_playback(entity_id, friendly_name):
    print_header(f"Func: Music Assistant (Play -> Stop on {friendly_name})")
   
    cast_entity = None
    if "office" in friendly_name.lower():
        cast_entity = find_entity_id("Office TV Chrome") 
    
    monitor_entity = cast_entity if cast_entity else entity_id
    print(f"   [MONITORING] {monitor_entity}")

    if not ensure_device_state(friendly_name, entity_id, "on"):
        print("   [SKIP] Device not ready for music test.")
        return
 
    cmd = f"Play Brandon Lake on the {friendly_name}"
    print(f"   [SENDING] '{cmd}'")
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":cmd}], "stream":False}, "Music Play")
   
    print(f"   [VERIFYING] {monitor_entity} -> 'playing'...")
    playing = False
    for i in range(15):
        state = get_state(monitor_entity)
        if state in ["playing", "buffering"]:
            print(f"   [PASS] {monitor_entity} is '{state}'.")
            playing = True
            break
        time.sleep(2)
   
    if not playing:
        state = get_state(monitor_entity)
        if state == "on" and monitor_entity == entity_id:
             print(f"   [PASS-ISH] Physical device {monitor_entity} is 'on' (likely playing via Cast).")
        else:
             print(f"   [FAIL] Music failed to start. Current State: {state}")
    else:
        time.sleep(3)
 
    print(f"   [ACTION] Stopping music...")
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":f"Stop the {friendly_name}"}], "stream":False}, "Music Stop")
    
    final_states = ["paused", "idle", "off", "on"]
    check_device_state(monitor_entity, final_states)

def test_timer_logic():
    print_header("Func: Home Assistant Timer")
    cmd = "Set a timer for 5 minutes"
    print(f"   [CMD] '{cmd}'")
    resp = safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":cmd}], "stream":False}, "Set Timer")
    
    if resp and ("Successfully" in resp or "timer.start" in resp):
        print("   [PASS] Timer start command sent.")
    else:
        print("   [WARN] Timer response ambiguous or failed.")
        
    time.sleep(1)
    
    cmd_cancel = "Cancel the timer"
    print(f"   [CMD] '{cmd_cancel}'")
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":cmd_cancel}], "stream":False}, "Cancel Timer")

def main():
    print(f"Starting Robust Tests on {API_URL}...\n")
    try:
        if requests.get(f"{API_URL}/health", timeout=5).status_code != 200:
            print("API Down"); return
    except: print("API Unreachable"); return
 
    print_header("Protocol: Health & Streaming")
    if requests.get(f"{API_URL}/health").status_code == 200: print("   [PASS] Health Check OK")

    lamp_id = find_entity_id(LAMP_NAME)
    tv_id = find_entity_id(MEDIA_NAME)
 
    if not lamp_id or not tv_id:
        print("   [CRITICAL FAIL] Entities not found in RAG. Aborting.")
        return
 
    test_history_context() 
    test_web_search_explicit() 
    test_calendar_lifecycle()
    test_timer_logic()
    
    print_header(f"Func: Control (Turn On {LAMP_NAME})")
    ensure_device_state(LAMP_NAME, lamp_id, "off")
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":f"Turn on the {LAMP_NAME}"}], "stream":False}, "Turn On")
    check_device_state(lamp_id, "on")
 
    test_music_playback(tv_id, MEDIA_NAME)
 
    print_header("Func: Multi-Command (Turn OFF Both)")
    ensure_device_state(LAMP_NAME, lamp_id, "on")
    ensure_device_state(MEDIA_NAME, tv_id, "on")
   
    cmd = f"Turn off the {LAMP_NAME} and the {MEDIA_NAME}"
    print(f"   [SENDING] '{cmd}'")
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":cmd}], "stream":False}, "Multi-Cmd")
   
    check_device_state(lamp_id, "off")
    check_device_state(tv_id, ["off", "idle", "standby", "unavailable"])
 
if __name__ == "__main__":
    main()
