import os
import time

import requests
from dotenv import load_dotenv

# Setup
script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
load_dotenv(os.path.join(root_dir, '.env'))

API_URL = os.getenv("API_URL", "http://ai.local:11435")
HEADERS = {"Content-Type": "application/json", "X-RAG-User": "admin"}
SILENT_TOKEN = "[SILENT_SUCCESS]"

def log(msg):
    print(msg, flush=True)
    with open("live_test_results.txt", "a") as f:
        f.write(msg + "\n")

def get_state(entity_id):
    try:
        r = requests.get(f"{API_URL}/api/ha/state/{entity_id}", headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.json().get("state", "unknown")
    except Exception:
        pass
    return "unknown"

def send_chat(content):
    try:
        r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":content}], "stream":False}, headers=HEADERS, timeout=30)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def _safe_msg(resp):
    if isinstance(resp, dict):
        msg = resp.get("message")
        if isinstance(msg, dict):
            return msg.get("content", "")
    return ""

def test_hardware_routing():
    log("\n--- TEST: Hardware Routing (Turn Off TV) ---")
    # This should target androidtv/cast, NOT music_assistant
    resp = send_chat("Turn off the Office TV")
    msg = _safe_msg(resp) or str(resp)

    if SILENT_TOKEN in msg:
        log("   [PASS] Silent Success token received.")
    else:
        log(f"   [INFO] Response: {msg}")

    # Verify state (Android TV should be off/standby)
    # Note: We can't check 'media_player.office_tv_mass' state here reliably as it mirrors,
    # but we can check the known android entity if we know it.
    # Assuming 'media_player.office_tv_android' exists based on previous logs.
    state = get_state("media_player.office_tv_android")
    log(f"   [VERIFY] Android TV State: {state}")
    if state in ["off", "standby", "idle", "unavailable"]:
         log("   [PASS] TV appears OFF.")
    else:
         log("   [FAIL] TV state is still " + state)

def test_audio_routing():
    log("\n--- TEST: Audio Routing (Play Music) ---")
    resp = send_chat("Play Brandon Lake on the Office TV")
    log(f"   [INFO] Command Sent. Response: {_safe_msg(resp)}")

    # Wait for state update
    time.sleep(2)

    # Check if Music Assistant entity is playing
    # We guess the ID 'media_player.office_tv_mass' or similar
    mass_state = get_state("media_player.office_tv_mass")
    log(f"   [VERIFY] Music Assistant State: {mass_state}")

    if mass_state in ["playing", "buffering"]:
        log("   [PASS] Music Assistant is active.")
    else:
        log("   [WARN] Music Assistant not playing (might be queuing or mismatch).")

    # Cleanup
    send_chat("Stop the Office TV")

def test_silent_success():
    log("\n--- TEST: Silent Success (Light) ---")
    # Turn on a light (Piano Lamp)
    resp = send_chat("Turn on the Piano Lamp")
    msg = _safe_msg(resp)

    if SILENT_TOKEN in msg:
        log("   [PASS] Silent Success validated on Light.")
    else:
        log(f"   [FAIL] Expected Silent Token, got: {msg}")

    send_chat("Turn off the Piano Lamp")

if __name__ == "__main__":
    # Clear previous log
    with open("live_test_results.txt", "w") as f: f.write(f"Test Run: {time.ctime()}\n")

    log(f"Targeting API: {API_URL}")
    test_hardware_routing()
    time.sleep(2)
    test_audio_routing()
    time.sleep(2)
    test_silent_success()
