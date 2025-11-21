import requests
import json
import time
import sys
import os
from dotenv import load_dotenv

script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
env_path = os.path.join(root_dir, '.env')
load_dotenv(env_path)

API_URL = os.getenv("API_URL", "http://localhost:11435")
HEADERS = {"Content-Type": "application/json", "X-RAG-User": "TestAdmin", "User-Agent": "TestScript"}

# Entities
LAMP_ENTITY = "light.piano_lamp"
MEDIA_ENTITY = "media_player.office_tv_chrome_2" 

def print_header(title):
    print(f"\n{'='*60}\nTEST: {title}\n{'='*60}")

def check_device_state(entity_id, expected_states, retries=3):
    if isinstance(expected_states, str): expected_states = [expected_states]
    print(f"   [VERIFYING] {entity_id} -> {expected_states}...")
    for i in range(retries):
        try:
            r = requests.get(f"{API_URL}/api/ha/state/{entity_id}", headers=HEADERS, timeout=5)
            if r.status_code == 200:
                state = r.json().get("state")
                if state in expected_states:
                    print(f"   [PASS] {entity_id} is '{state}'.")
                    return True
                else:
                    print(f"   [WAIT] {entity_id} is '{state}'. Retry {i+1}...")
        except: pass
        time.sleep(2)
    print(f"   [FAIL] {entity_id} state mismatch.")
    return False

def safe_post(url, payload, label):
    try:
        return requests.post(url, json=payload, headers=HEADERS, timeout=120)
    except Exception as e:
        print(f"   [CRITICAL FAIL] {label} Timed out or Error: {e}")
        return None

def test_protocol_compliance():
    print_header("Protocol: OpenAI SSE Streaming (/v1/)")
    try:
        r = requests.post(f"{API_URL}/v1/chat/completions", json={"model":"qwen3:latest","messages":[{"role":"user","content":"Hi"}],"stream":True}, stream=True, timeout=120)
        if any(line.decode().startswith("data: ") for line in r.iter_lines()): print("   [PASS] Received SSE.")
        else: print("   [FAIL] No SSE data.")
    except Exception as e: print(f"   [FAIL] {e}")

    print_header("Protocol: Ollama NDJSON Streaming (/api/chat)")
    try:
        r = requests.post(f"{API_URL}/api/chat", json={"model":"qwen3:latest","messages":[{"role":"user","content":"Hi"}],"stream":True}, stream=True, timeout=120)
        lines = [line for line in r.iter_lines() if line]
        if lines and "message" in json.loads(lines[0]): print("   [PASS] Received Ollama NDJSON.")
        else: print(f"   [FAIL] Invalid JSON stream.")
    except Exception as e: print(f"   [FAIL] {e}")

def test_functionality():
    print_header("Func: Context")
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":"Status of Piano Lamp?"}], "stream":False}, "Context")

    print_header("Func: Control (Turn On)")
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":"Turn on the Piano Lamp"}], "stream":False}, "Turn On")
    check_device_state(LAMP_ENTITY, "on")

    print_header("Func: Multi-Command (Turn OFF Both)")
    # Ensure ON first
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":"Turn on the Piano Lamp"}], "stream":False}, "Setup Lamp")
    time.sleep(1)
    
    # The big test
    safe_post(f"{API_URL}/api/chat", {"messages":[{"role":"user","content":"Turn off the Piano Lamp and the Office TV"}], "stream":False}, "Multi-Cmd")
    
    check_device_state(LAMP_ENTITY, "off")
    check_device_state(MEDIA_ENTITY, ["off", "idle", "standby"])

def main():
    print(f"Starting Tests on {API_URL}...\n")
    try:
        if requests.get(f"{API_URL}/health", timeout=5).status_code != 200:
            print("API Down"); return
    except: print("API Unreachable"); return

    test_protocol_compliance()
    test_functionality()
    print("\nTEST SEQUENCE COMPLETE")

if __name__ == "__main__":
    main()
