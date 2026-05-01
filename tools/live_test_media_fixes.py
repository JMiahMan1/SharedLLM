
import requests
import time
import json
import sys

# Configuration
SERVER_URL = "http://ai.local:11435" 
CHAT_ENDPOINT = f"{SERVER_URL}/api/chat"
HEALTH_ENDPOINT = f"{SERVER_URL}/health"
HA_STATE_ENDPOINT = f"{SERVER_URL}/api/ha/state" # /{entity_id}

# Device Constants
ROKU_ID = "media_player.roku_2n0062385487" # Gracie's TV
ANDROID_ID = "media_player.office_tv_chrome_2" # Office TV (Cast)

def get_entity_state(entity_id):
    try:
        r = requests.get(f"{HA_STATE_ENDPOINT}/{entity_id}", timeout=5)
        if r.status_code == 200:
            data = r.json()
            return data.get("state")
    except Exception as e:
        print(f"  [Warn] Failed to get state for {entity_id}: {e}")
    return None

def wait_for_state(entity_id, expected_states, timeout=20):
    """Waits for entity to reach one of the expected states."""
    if isinstance(expected_states, str):
        expected_states = [expected_states]
        
    print(f"  > Waiting for {entity_id} to be {expected_states}...", end="", flush=True)
    
    for _ in range(timeout):
        current_state = get_entity_state(entity_id)
        if current_state in expected_states:
            print(f" OK ({current_state})")
            return True
        
        # Debug output: print first char of state
        char = "?"
        if current_state: char = current_state[0]
        print(char, end="", flush=True)
        time.sleep(1)
        
    print(f" Timeout! Current: {get_entity_state(entity_id)}")
    return False

def send_chat(query, user="admin"):
    print(f"\n[CMD] '{query}'")
    try:
        res = requests.post(CHAT_ENDPOINT, json={"query": query, "user": user}, timeout=120)
        if res.status_code == 200:
            data = res.json()
            resp_text = ""
            if "response" in data:
                resp_text = data["response"]
            elif "message" in data and "content" in data["message"]:
                resp_text = data["message"]["content"]
            elif "choices" in data and len(data["choices"]) > 0:
                resp_text = data["choices"][0]["message"]["content"]
            else:
                resp_text = str(data)
                
            print(f"  [Resp] {resp_text}")
            return data
        else:
            print(f"  [Error] HTTP {res.status_code}: {res.text}")
            return None
    except Exception as e:
        print(f"  [Exception] {e}")
        return None

def ensure_off(entity_id):
    print(f"  > Ensuring {entity_id} is OFF...", end="", flush=True)
    state = get_entity_state(entity_id)
    if state in ["off", "standby", "idle"]:
        print(" Already OFF.")
        return True
    
    # Send turn off command
    send_chat(f"Turn off {entity_id}") 
    return wait_for_state(entity_id, ["off", "standby", "idle"])

def run_scenario(name, steps):
    print(f"\n{'='*60}")
    print(f"=== Scenario: {name} ===")
    print(f"{'='*60}")
    
    for step in steps:
        if step.get("action") == "ensure_off":
            if not ensure_off(step["entity"]):
                print("  [FAIL] Could not turn off device to start test.")
                return False
            continue
        
        if step.get("action") == "wait":
            wait_time = step.get("seconds", 5)
            print(f"\n  > Waiting {wait_time}s...")
            time.sleep(wait_time)
            continue

        cmd = step["cmd"]
        entity = step.get("entity")
        expect_state = step.get("expect_state")
        
        # Send Command
        data = send_chat(cmd)
        if not data:
            print("  [FAIL] Command failed.")
            return False
            
        # Verify State
        if entity and expect_state:
            if not wait_for_state(entity, expect_state, timeout=step.get("timeout", 25)):
                print("  [FAIL] State verification failed.")
                return False
                
        time.sleep(1) # Brief pause between steps
        
    print(f"\n{'='*60}")
    print(f"=== {name} PASSED ===")
    print(f"{'='*60}\n")
    return True

# ====================================================================================
# TEST SCENARIOS - Play vs Watch Intent on Both Devices
# ====================================================================================

# ROKU - PLAY INTENT (Music via Music Assistant)
ROKU_PLAY_TEST = [
    { "action": "ensure_off", "entity": ROKU_ID },
    { "cmd": "Play Brandon Lake on Gracie's TV", "entity": ROKU_ID, "expect_state": ["playing", "buffering"], "timeout": 30 },
    { "action": "wait", "seconds": 15 },  # Let it play for 15s
    { "cmd": "Pause", "entity": ROKU_ID, "expect_state": ["paused", "idle"] },
    { "action": "wait", "seconds": 10 },  # Paused for 10s
    { "cmd": "Resume", "entity": ROKU_ID, "expect_state": ["playing", "buffering"] },
    { "action": "wait", "seconds": 5 },  # Resume for 5s
    { "cmd": "Stop", "entity": ROKU_ID, "expect_state": ["idle", "off", "paused", "standby"] },
    { "cmd": "Turn off Gracie's TV", "entity": ROKU_ID, "expect_state": ["off", "standby", "idle"] }
]

# ROKU - WATCH INTENT (Video)
ROKU_WATCH_TEST = [
    { "action": "ensure_off", "entity": ROKU_ID },
    { "cmd": "Watch Tim Timmons on Gracie's TV", "entity": ROKU_ID, "expect_state": ["playing", "buffering"], "timeout": 30 },
    { "action": "wait", "seconds": 5 },  # Let it play for 5s
    { "cmd": "Pause", "entity": ROKU_ID, "expect_state": ["paused", "idle", "off"] },
    { "cmd": "Resume", "entity": ROKU_ID, "expect_state": ["playing", "buffering"] },
    { "action": "wait", "seconds": 5 },  # Resume for 5s
    { "cmd": "Stop", "entity": ROKU_ID, "expect_state": ["idle", "off", "paused", "standby"] },
    { "cmd": "Turn off Gracie's TV", "entity": ROKU_ID, "expect_state": ["off", "standby", "idle"] }
]

# ANDROID TV - PLAY INTENT (Music via Music Assistant)
ANDROID_PLAY_TEST = [
    { "action": "ensure_off", "entity": ANDROID_ID },
    { "cmd": "Play Brandon Lake on Office TV", "entity": ANDROID_ID, "expect_state": ["playing", "buffering"], "timeout": 30 },
    { "action": "wait", "seconds": 15 },  # Let it play for 15s
    { "cmd": "Pause", "entity": ANDROID_ID, "expect_state": ["paused", "idle"] },
    { "action": "wait", "seconds": 10 },  # Paused for 10s
    { "cmd": "Resume", "entity": ANDROID_ID, "expect_state": ["playing", "buffering"] },
    { "action": "wait", "seconds": 5 },  # Resume for 5s
    { "cmd": "Stop", "entity": ANDROID_ID, "expect_state": ["idle", "off", "paused", "standby"] },
    { "cmd": "Turn off Office TV", "entity": ANDROID_ID, "expect_state": ["off", "standby", "idle"] }
]

# ANDROID TV - WATCH INTENT (Video - may not work on Cast, but test it)
ANDROID_WATCH_TEST = [
    { "action": "ensure_off", "entity": ANDROID_ID },
    { "cmd": "Watch Tim Timmons on Office TV", "entity": ANDROID_ID, "expect_state": ["playing", "buffering"], "timeout": 30 },
    { "action": "wait", "seconds": 5 },  # Let it play for 5s
    { "cmd": "Pause", "entity": ANDROID_ID, "expect_state": ["paused", "idle", "off"] },
    { "cmd": "Resume", "entity": ANDROID_ID, "expect_state": ["playing", "buffering"] },
    { "action": "wait", "seconds": 5 },  # Resume for 5s
    { "cmd": "Stop", "entity": ANDROID_ID, "expect_state": ["idle", "off", "paused", "standby"] },
    { "cmd": "Turn off Office TV", "entity": ANDROID_ID, "expect_state": ["off", "standby", "idle"] }
]
# FUZZY MATCHING TESTS
FUZZY_TEST = [
    { "action": "ensure_off", "entity": ROKU_ID },
    # Test 1: "Brendan Lak" -> Brandon Lake
    { "cmd": "Play Brendan Lak on Gracie's TV", "entity": ROKU_ID, "expect_state": ["playing", "buffering"], "timeout": 35 },
    { "action": "wait", "seconds": 10 },
    { "cmd": "Stop", "entity": ROKU_ID, "expect_state": ["idle", "off", "paused", "standby"] },
    
    # Test 2: "The Weekend" -> The Weeknd (common spelling error)
    { "cmd": "Play The Weekend on Gracie's TV", "entity": ROKU_ID, "expect_state": ["playing", "buffering"], "timeout": 35 },
    { "action": "wait", "seconds": 10 },
    { "cmd": "Stop", "entity": ROKU_ID, "expect_state": ["idle", "off", "paused", "standby"] },
    { "cmd": "Turn off Gracie's TV", "entity": ROKU_ID, "expect_state": ["off", "standby", "idle"] }
]
def clear_context():
    """Clear Redis context (last entity, last media entity) to prevent test crossover."""
    try:
        print("  \u003e Clearing Redis context...", end="", flush=True)
        # Call API endpoint to clear context for admin user
        payload = {"user": "admin"}
        res = requests.post(f"{SERVER_URL}/api/context/clear", json=payload, timeout=5)
        if res.status_code == 200:
            print(" OK")
            return True
        else:
            print(f" Failed ({res.status_code})")
            return False
    except Exception as e:
        print(f" Error: {e}")
        return False

def wait_for_server():
    print(f"Waiting for server at {SERVER_URL}...")
    start = time.time()
    while time.time() - start < 300:
        try:
            r = requests.get(HEALTH_ENDPOINT, timeout=2)
            if r.status_code == 200:
                print("Server is UP!")
                return True
        except:
            pass
        time.sleep(5)
        print(".", end="", flush=True)
    return False

if __name__ == "__main__":
    if not wait_for_server():
        sys.exit(1)
        
    results = []
    
    # Run all test scenarios
    if run_scenario("Roku - Play Intent (Music)", ROKU_PLAY_TEST):
        results.append("Roku Play: PASS")
    else:
        results.append("Roku Play: FAIL")
    
    # Clear context before next scenario
    clear_context()    
    time.sleep(5)
        
    if run_scenario("Roku - Watch Intent (Video)", ROKU_WATCH_TEST):
        results.append("Roku Watch: PASS")
    else:
        results.append("Roku Watch: FAIL")
    
    # Clear context before next scenario
    clear_context()    
    time.sleep(5)
          
    if run_scenario("Android TV - Play Intent (Music)", ANDROID_PLAY_TEST):
        results.append("Android Play: PASS")
    else:
        results.append("Android Play: FAIL")
    
    # Clear context before next scenario
    clear_context()    
    time.sleep(5)
    
    if run_scenario("Android TV - Watch Intent (Video)", ANDROID_WATCH_TEST):
        results.append("Android Watch: PASS")
    else:
        results.append("Android Watch: FAIL")
        
    # Clear context before next scenario
    clear_context()    
    time.sleep(5)
    
    if run_scenario("Fuzzy Name Matching", FUZZY_TEST):
        results.append("Fuzzy Match: PASS")
    else:
        results.append("Fuzzy Match: FAIL")
        
    print("\n" + "="*60)
    print("FINAL SUMMARY:")
    print("="*60)
    for r in results: 
        print(f"  {r}")
    print("="*60)
    
    if any("FAIL" in r for r in results):
        sys.exit(1)
    sys.exit(0)

