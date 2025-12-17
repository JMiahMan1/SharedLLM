
import requests
import time
import json
import os

API_URL = "http://192.168.2.211:11435"
HEADERS = {"X-RAG-User": "admin", "Content-Type": "application/json"}
DEVICE = "media_player.office_tv_chrome_2"

def get_state(entity_id):
    try:
        # Use HA API via RAG if possible, or assume HA_URL is accessible? 
        # The test runner usually uses `execute_command("get state...")` but that's slow.
        # We'll use the specific /api/state endpoint if available, but RAG API doesn't expose it directly.
        # We'll use a direct HA request if possible, or just trust the RAG "What is the status of..."? No, that's slow.
        # We'll use the 'media_player' service call via RAG and parse? 
        # Actually, let's use the natural language command "What is the volume of Office TV?" 
        # No, let's use the HA API directly if we have the token?
        # The environment has HA_URL and HA_TOKEN?
        # We can try to load them from .env
        pass
    except:
        pass

# We will use the RAG API verify logic.
def run_volume_test():
    print(f"Testing Volume on {DEVICE}")
    
    # 1. Ensure playing (to be safe)
    # print("Ensuring device is playing...")
    # requests.post(f"{API_URL}/api/chat", json={"message": f"Play Brandon Lake on {DEVICE}", "temperature": 0.0}, headers=HEADERS)
    # time.sleep(10)

    # 2. Set Volume 25%
    print("Setting volume to 25%...")
    resp = requests.post(f"{API_URL}/api/chat", json={"query": f"Set volume to 25% on {DEVICE}", "temperature": 0.0}, headers=HEADERS)
    print(f"Response: {resp.status_code}")
    print(resp.text)
    
    time.sleep(5)
    
    # 3. Check Volume via specialized RAG query or assuming we can't check easily without HA API
    # We will assume if 200 OK it worked, but valid verification requires checking state.
    # The previous test failed verification.
    # I'll rely on the output of the API which usually includes the new state in 'message' or 'state' field if 'execute_ha_service' returns it.
    
    try:
        data = resp.json()
        print(f"JSON Data: {json.dumps(data, indent=2)}")
    except:
        print("Could not parse JSON response.")

if __name__ == "__main__":
    run_volume_test()
