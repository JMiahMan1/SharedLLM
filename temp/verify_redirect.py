
import requests
import json
import time
import os

API_URL = "http://192.168.2.211:11435"
HEADERS = {"X-RAG-User": "admin", "Content-Type": "application/json"}
CAST_ENTITY = "media_player.office_tv_chrome_2"
TV_ENTITY = "media_player.office_tv"

def get_ha_headers():
    # Helper to get HA token from settings (simulated)
    # We will just fetch them from a helper or assume we can't easily.
    # We will rely on the RAG API responses.
    return HEADERS

def run_test():
    print(f"--- Testing Redirection from {CAST_ENTITY} to {TV_ENTITY} ---")
    
    # 1. Test Volume Redirection
    print("\n[TEST 1] Volume Set on Cast Entity -> Should affect logical TV or redirected target")
    # First, turn on if needed?
    # Sending 'Turn on Office TV'
    # requests.post(f"{API_URL}/api/chat", json={"query": f"Turn on {TV_ENTITY}", "temperature":0.0}, headers=HEADERS)
    # time.sleep(2)
    
    target_vol = 0.25
    print(f"Sending: Set volume to {int(target_vol*100)}% on {CAST_ENTITY}")
    resp = requests.post(f"{API_URL}/api/chat", json={"query": f"Set volume to {int(target_vol*100)}% on {CAST_ENTITY}", "temperature": 0.0}, headers=HEADERS)
    print(f"Response: {resp.status_code} - {resp.text}")
    
    time.sleep(2)
    
    # Check TV Entity Volume (we can't easily check HA state directly without token, but we can infer from logs or just trust it for now)
    # Actually, let's use the debug script logic to fetch state!
    # verifying...
    
    # 2. Test Turn Off Redirection
    print("\n[TEST 2] Turn Off Cast Entity -> Should Turn Off TV")
    print(f"Sending: Turn off {CAST_ENTITY}")
    resp = requests.post(f"{API_URL}/api/chat", json={"query": f"Turn off {CAST_ENTITY}", "temperature": 0.0}, headers=HEADERS)
    print(f"Response: {resp.status_code} - {resp.text}")

if __name__ == "__main__":
    try:
        run_test()
    except Exception as e:
        print(f"Error: {e}")
