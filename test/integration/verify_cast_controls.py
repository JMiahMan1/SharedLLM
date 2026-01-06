
import sys
import os
import requests
import time
# Ensure project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.settings import HA_ENV_TOKEN as HA_TOKEN, HA_URL
# If HA_URL not in settings, fallback to env (settings usually has it)
if not HA_URL:
    HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")

if not HA_TOKEN:
     HA_TOKEN = os.getenv("HA_TOKEN", "")

# --- CONFIGURATION ---
API_URL = os.getenv("API_URL", "http://192.168.2.211:11435")

DEVICE_ENTITY = "media_player.office_tv_chrome_2"
DEVICE_NAME = "Office TV"

# Test Data
VIDEO_URL = "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"

# Force Headers
HEADERS = {"X-RAG-User": "admin", "Content-Type": "application/json"}
HA_HEADERS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

def log(msg):
    print(f"[TEST] {msg}", flush=True)

def get_ha_info():
    try:
        r = requests.get(f"{HA_URL}/api/states/{DEVICE_ENTITY}", headers=HA_HEADERS, timeout=5)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        log(f"HA Info check failed: {e}")
        return None

def get_ha_state():
    d = get_ha_info()
    return d.get("state", "error") if d else "error"

def log_status(tag):
    d = get_ha_info()
    if d:
        state = d.get("state", "unknown")
        attrs = d.get("attributes", {})
        title = attrs.get("media_title", "N/A")
        app = attrs.get("app_id", attrs.get("app_name", "N/A"))
        vol = attrs.get("volume_level", "N/A")
        log(f"[{tag}] Status: State={state} | Title='{title}' | App='{app}' | Vol={vol}")
    else:
        log(f"[{tag}] Status: Unreachable")

def send_chat_command(msg):
    log(f"[COMMAND] Sending Voice Query: '{msg}'...")
    try:
        r = requests.post(f"{API_URL}/api/chat", json={"messages": [{"role": "user", "content": msg}]}, headers=HEADERS, timeout=60)
        return r.status_code == 200
    except Exception as e:
        log(f"[COMMAND] Failed: {e}")
        return False

def force_ha_turn_off():
    log(f"[RESET] Forcing TURN OFF on {DEVICE_ENTITY} via HA API...")
    data = {"entity_id": DEVICE_ENTITY}
    try:
        requests.post(f"{HA_URL}/api/services/media_player/turn_off", headers=HA_HEADERS, json=data, timeout=10)
    except Exception as e:
        log(f"[RESET] Force turn_off failed: {e}")

def force_ha_play():
    log(f"[SETUP] Forcing playback of 'Big Buck Bunny' on {DEVICE_ENTITY} via HA API...")
    data = {
        "entity_id": DEVICE_ENTITY,
        "media_content_id": VIDEO_URL,
        "media_content_type": "video",
    }
    try:
        requests.post(f"{HA_URL}/api/services/media_player/play_media", headers=HA_HEADERS, json=data, timeout=10)
    except Exception as e:
        log(f"[SETUP] Force play failed: {e}")

def force_ha_stop():
    log(f"[RESET] Forcing stop on {DEVICE_ENTITY} via HA API...")
    data = {"entity_id": DEVICE_ENTITY}
    try:
        requests.post(f"{HA_URL}/api/services/media_player/media_stop", headers=HA_HEADERS, json=data, timeout=10)
    except Exception as e:
        log(f"[RESET] Force stop failed: {e}")

def wait_for_state(target_states, timeout=20):
    if isinstance(target_states, str): target_states = [target_states]
    log(f"Waiting for state in {target_states} (timeout={timeout}s)...")
    for _ in range(timeout):
        s = get_ha_state()
        if s in target_states:
            log(f"✅ State REACHED: {s}")
            return s
        time.sleep(1)
    log(f"❌ State TIMEOUT. Current: {s}")
    return s

# --- MAIN TEST SEQUENCE ---

print("==================================================")
print(" ROBUST INTEGRATION VERIFICATION: OFICE TV CONTROLS")
print("==================================================")

# 1. RESET PHASE
log("--- PHASE 1: RESET (Enforcing OFF State) ---")
initial = get_ha_state()
log(f"Initial State: {initial}")
log_status("PRE_RESET")

# Standardize to OFF first
force_ha_turn_off()
wait_for_state(["off", "standby"], timeout=15)
log_status("POST_RESET")

# 2. SETUP PHASE
log("\n--- PHASE 2: SETUP (Start Playback from OFF) ---")
force_ha_play()
state = wait_for_state(["playing", "buffering"], timeout=20)
if state not in ["playing", "buffering"]:
    log("CRITICAL FAILURE: Could not start media for test. Aborting.")
    sys.exit(1)
if state == "buffering":
    log("Buffering... waiting for Playing...")
    state = wait_for_state("playing", timeout=10)
log_status("SETUP_DONE")

# 3. VERIFY PAUSE (The Fix Check)
log("\n--- PHASE 3: VERIFY PAUSE ROUTING ---")
# Use explicit phrasing to avoid any ambiguity
send_chat_command(f"Pause music on {DEVICE_NAME}")
# Allow generous time for Cast latency
final_pause = wait_for_state("paused", timeout=15)
log_status("POST_PAUSE")
if final_pause == "paused":
    log("✅ SUCCESS: Device successfully PAUSED via RAG API.")
else:
    log(f"❌ FAILURE: Device did not pause. State: {final_pause}")

# 4. VERIFY RESUME
log("\n--- PHASE 4: VERIFY RESUME ROUTING ---")
send_chat_command(f"Resume music on {DEVICE_NAME}")
final_resume = wait_for_state("playing", timeout=15)
log_status("POST_RESUME")
if final_resume == "playing":
    log("✅ SUCCESS: Device successfully RESUMED via RAG API.")
else:
    log(f"❌ FAILURE: Device did not resume. State: {final_resume}")

# 5. VERIFY STOP (Teardown)
log("\n--- PHASE 5: VERIFY STOP ROUTING ---")
send_chat_command(f"Stop music on {DEVICE_NAME}")
final_stop = wait_for_state(["idle", "off", "standby"], timeout=15)
log_status("POST_STOP")
if final_stop in ["idle", "off", "standby"]:
    log("✅ SUCCESS: Device successfully STOPPED via RAG API.")
else:
    log(f"❌ FAILURE: Device did not stop. State: {final_stop}")

print("\n==================================================")
print(" TEST COMPLETE")
print("==================================================")
