import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()
RAG_ADDRESS = os.getenv("RAG_ADDRESS")

if not RAG_ADDRESS:
    print("ERROR: RAG_ADDRESS not found in .env")
    sys.exit(1)

BASE_URL = f"http://{RAG_ADDRESS}:11435"
HEADERS = {"X-RAG-User": "admin"}

# Map friendly names to likely entity_ids (will verify/discover)
DEVICE_MAP = {
    "Office TV": ["media_player.office_tv", "media_player.office_tv_chrome_2"],
    "Master Bedroom TV": ["media_player.master_bedroom_tv", "media_player.master_bedroom_tv_adb"],
    "Gracies TV": ["media_player.gracies_tv", "media_player.gracie_s_tv"]
}

def get_ha_state(entity_id):
    """Fetch state from RAG API proxy."""
    try:
        resp = requests.get(f"{BASE_URL}/api/ha/state/{entity_id}", headers=HEADERS, timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"  [WARN] Failed to fetch state for {entity_id}: {e}")
    return None

def find_best_entity(friendly_name):
    """Try to find the working entity_id for a friendly name."""
    candidates = DEVICE_MAP.get(friendly_name, [])
    for eid in candidates:
        state = get_ha_state(eid)
        if state and "error" not in state:
            return eid, state
    return None, None

def check_device_status(friendly_name):
    print(f"  [CHECK] Status for '{friendly_name}'...")
    eid, state = find_best_entity(friendly_name)
    if not eid:
        print(f"  [FAIL] Could not find valid entity for {friendly_name}")
        return None, None

    val = state.get("state")
    print(f"    Found: {eid} | State: {val} | Attrs: {state.get('attributes', {}).get('media_title', 'No Media')}")
    return eid, val

def run_test_strict(command, device_name, action_type, expected_state=None):
    print(f"\n--- Testing: {command} ---")

    # 1. Pre-Check
    eid, pre_state = check_device_status(device_name)
    if not eid:
        print("  [SKIP] Device not found/available. Cannot test.")
        return False

    # 2. Execute
    print("  [EXEC] Sending command...")
    start = time.time()
    try:
        resp = requests.post(f"{BASE_URL}/api/chat", json={"query": command}, headers=HEADERS, timeout=30)
        dur = time.time() - start
    except Exception as e:
        print(f"  [FAIL] Request Error: {e}")
        return False

    if resp.status_code != 200:
        print(f"  [FAIL] HTTP {resp.status_code}: {resp.text}")
        return False

    data = resp.json()
    msg = data.get("message", {}).get("content", "") or data.get("response", "")
    print(f"  [API] Response ({dur:.2f}s): {msg[:100]}...")

    # Check for negative LLM responses
    if any(x in msg.lower() for x in ["fail", "error", "connect", "unreachable", "offline", "cannot"]):
        print(f"  [FAIL] LLM reported failure: {msg}")
        return False

    # 3. Post-Check & Verify
    # Wait a moment for state update
    time.sleep(3)
    _, post_state = check_device_status(device_name)

    if action_type == "play":
        # Expect playing or buffering
        if post_state in ["playing", "buffering"]:
            print(f"  [PASS] Device is {post_state}.")
            return True
        elif post_state == "idle" and pre_state == "off":
             print("  [WARN] Device turned on but is idle (maybe loading?).")
             return True # Partial pass
        else:
            print(f"  [FAIL] Expected playing, got {post_state}.")
            return False

    elif action_type == "stop":
        # Expect idle, paused, or off
        if post_state in ["idle", "paused", "off", "standby"]:
            print(f"  [PASS] Device is {post_state}.")
            return True
        else:
            print(f"  [FAIL] Expected stop/idle, got {post_state}.")
            return False

    return True

def main():
    print(f"Target: {BASE_URL}")

    # Check Devices First
    print("\n--- Device Discovery ---")
    for name in DEVICE_MAP:
        check_device_status(name)

    # Test Loop
    passes = 0
    fails = 0

    # 1. Office TV - Music
    if run_test_strict("Play Brandon Lake on Office TV", "Office TV", "play"): passes += 1
    else: fails += 1

    time.sleep(5)

    if run_test_strict("Stop Office TV", "Office TV", "stop"): passes += 1
    else: fails += 1

    # 2. Office TV - Video
    if run_test_strict("Watch Big Buck Bunny on Office TV", "Office TV", "play"): passes += 1
    else: fails += 1

    time.sleep(5)

    if run_test_strict("Stop Office TV", "Office TV", "stop"): passes += 1
    else: fails += 1

    # 3. Gracie
    if run_test_strict("Play generic music on Gracies TV", "Gracies TV", "play"): passes += 1
    else: fails += 1

    time.sleep(2)
    if run_test_strict("Stop Gracies TV", "Gracies TV", "stop"): passes += 1
    else: fails += 1

    print(f"\nSummary: {passes} Passed, {fails} Failed.")
    if fails > 0: sys.exit(1)

if __name__ == "__main__":
    main()
