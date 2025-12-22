
import requests
import time
import json
import sys

# Configuration
SERVER_URL = "http://192.168.2.211:11435" 
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

def wait_for_state(entity_id, expected_states, timeout=60):
    """Waits for entity to read one of the expected states."""
    if isinstance(expected_states, str):
        expected_states = [expected_states]
        
    print(f"  > Waiting for {entity_id} to be {expected_states}...", end="", flush=True)
    
    for _ in range(timeout):
        current_state = get_entity_state(entity_id)
        if current_state in expected_states:
            print(f" OK ({current_state})")
            return True
        
        # Debug output: print first char of state or ?
        char = "?"
        if current_state: char = current_state[0]
        print(char, end="", flush=True)
        time.sleep(1)
        
    print(f" Timeout! Current: {get_entity_state(entity_id)}")
    return False

def send_chat(query, user="admin"):
    print(f"\n[CMD] '{query}'")
    try:
        res = requests.post(CHAT_ENDPOINT, json={"query": query, "user": user}, timeout=60)
        if res.status_code == 200:
            data = res.json()
            # Handle various response formats
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
    if state in ["off", "standby"]:
        print(" Already OFF.")
        return True
    
    # Send turn off command
    send_chat(f"Turn off {entity_id}") 
    return wait_for_state(entity_id, ["off", "standby"])

def run_scenario(name, steps):
    print(f"\n=== Scenario: {name} ===")
    
    for step in steps:
        if step.get("action") == "ensure_off":
            if not ensure_off(step["entity"]):
                print("  [FAIL] Could not turn off device to start test.")
                return False
            continue

        cmd = step["cmd"]
        entity = step.get("entity")
        expect_state = step.get("expect_state")
        
        # Send Command
        data = send_chat(cmd)
        if not data:
            print("  [FAIL] Command failed.")
            return False
            
        # Verify Response Text (Optional keywords)
        if "verify_text" in step:
            if step["verify_text"] not in str(data):
                 print(f"  [FAIL] Text verification failed. Need '{step['verify_text']}'")
                 return False

        # Verify State
        if entity and expect_state:
            if not wait_for_state(entity, expect_state):
                print("  [FAIL] State verification failed.")
                return False
                
        time.sleep(2) # Stability pause
        
    print(f"=== {name} PASSED ===")
    return True

# --- SCENARIOS ---

SCENARIO_ROKU_MUSIC = [
    { "action": "ensure_off", "entity": ROKU_ID },
    {
        "cmd": "Play Brandon Lake on Gracie's TV",
        "entity": ROKU_ID,
        "expect_state": ["playing", "buffering", "on"], 
    },
    { "cmd": "Pause", "entity": ROKU_ID, "expect_state": "paused" },
    { "cmd": "Resume", "entity": ROKU_ID, "expect_state": "playing" },
    { "cmd": "Next", "entity": ROKU_ID, "expect_state": ["playing", "buffering"] },
    { "cmd": "Stop", "entity": ROKU_ID, "expect_state": ["idle", "standby", "off", "home", "paused"] }, 
    { "cmd": "Turn off Gracie's TV", "entity": ROKU_ID, "expect_state": ["off", "standby"] }
]

SCENARIO_ROKU_VIDEO = [
    { "action": "ensure_off", "entity": ROKU_ID },
    { 
        "cmd": "Watch Brandon Lake on Gracie's TV",
        "entity": ROKU_ID,
        "expect_state": ["playing", "buffering"] 
    },
    { "cmd": "Pause", "entity": ROKU_ID, "expect_state": "paused" },
    { "cmd": "Resume", "entity": ROKU_ID, "expect_state": "playing" },
    { "cmd": "Stop", "entity": ROKU_ID, "expect_state": ["idle", "standby", "off", "home", "paused"] },
    { "cmd": "Turn off Gracie's TV", "entity": ROKU_ID, "expect_state": ["off", "standby"] }
]

SCENARIO_ANDROID_CONTEXT = [
    { "action": "ensure_off", "entity": ANDROID_ID },
    { 
        "cmd": "Play music on Office TV", 
        "entity": ANDROID_ID,
        "expect_state": ["playing", "buffering"]
    },
    { 
        "cmd": "Skip", 
        "entity": ANDROID_ID,
        "expect_state": ["playing", "buffering"] 
    },
    { "cmd": "Stop", "entity": ANDROID_ID, "expect_state": ["idle", "off", "paused", "standby", "on"] },
    { "cmd": "Turn off Office TV", "entity": ANDROID_ID, "expect_state": ["off", "standby", "idle"] }
]

SCENARIO_FUZZY = [
    { "action": "ensure_off", "entity": ANDROID_ID },
    {
        "cmd": "Play Brenden Lak on Office TV",
        "entity": ANDROID_ID,
        "expect_state": ["playing", "buffering"],
        "verify_text": "Brandon Lake" 
    },
    { "cmd": "Stop", "entity": ANDROID_ID, "expect_state": ["idle", "off", "paused", "standby"] },
    {
        "cmd": "Play The Weeknd on Office TV",
        "entity": ANDROID_ID,
        "expect_state": ["playing", "buffering"],
        "verify_text": "The Weeknd"
    },
    { "cmd": "Stop", "entity": ANDROID_ID, "expect_state": ["idle", "off", "paused", "standby"] }
]

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
    
    if run_scenario("Roku Music Cycle", SCENARIO_ROKU_MUSIC):
        results.append("Roku Music: PASS")
    else:
        results.append("Roku Music: FAIL")
        
    time.sleep(5) 
        
    if run_scenario("Roku Video Cycle", SCENARIO_ROKU_VIDEO):
        results.append("Roku Video: PASS")
    else:
        results.append("Roku Video: FAIL")
        
    time.sleep(5) 
         
    if run_scenario("Android Context", SCENARIO_ANDROID_CONTEXT):
        results.append("Android Context: PASS")
    else:
        results.append("Android Context: FAIL")
        
    time.sleep(5)
    
    if run_scenario("Fuzzy Matching", SCENARIO_FUZZY):
        results.append("Fuzzy Matching: PASS")
    else:
        results.append("Fuzzy Matching: FAIL")
        
    print("\nSUMMARY:")
    for r in results: print(r)
    
    if any("FAIL" in r for r in results):
        sys.exit(1)
    sys.exit(0)
