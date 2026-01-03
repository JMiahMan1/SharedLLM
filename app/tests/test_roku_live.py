import requests
import time
import os
import sys

# Add parent directory to path to import app.tests.base
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from app.tests.base import BaseTest

# Configuration
# Load defaults, but allow override from .env
DEVICE_NAME = "Gracies TV"
ENTITY_ID = "media_player.gracies_tv" 

def load_env_vars():
    """Load creds and config from local .env"""
    env_path = os.path.join(os.path.dirname(__file__), '../../.env')
    config = {}
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                if '=' in line and not line.startswith('#'):
                    key, val = line.strip().split('=', 1)
                    config[key] = val
    return config

# Load config first
_config = load_env_vars()
API_URL = _config.get("API_URL", "http://192.168.2.211:11435")  # Default to port 11435 defined in settings.py/docker-compose

def get_ha_state(ha_url, token, entity_id):
    """Fetch current state from HA directly"""
    url = f"{ha_url}/api/states/{entity_id}"
    # Debug: Check if token exists
    if not token:
        print(f"DEBUG: HA_TOKEN is MISSING/EMPTY for {ha_url}")
        return None
        
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            return r.json().get("state")
        elif r.status_code == 404:
            print(f"DEBUG: Entity {entity_id} not found. Searching for candidates...")
            try:
                # Fallback: List all states to find the real ID
                r_all = requests.get(f"{ha_url}/api/states", headers=headers, timeout=10)
                if r_all.status_code == 200:
                    all_states = r_all.json()
                    candidates = [s['entity_id'] for s in all_states if 'media_player' in s['entity_id'] and ('gracie' in s['entity_id'].lower() or 'tv' in s['entity_id'].lower())]
                    print(f"DEBUG: Found candidates: {candidates}")
            except Exception as ex:
                print(f"DEBUG: Error listing states: {ex}")
        else:
            print(f"DEBUG: HA API Failed. Status: {r.status_code}, Resp: {r.text[:100]}")
    except Exception as e:
        print(f"DEBUG: Error fetching HA state: {e}")
    return None

def resolve_entity_by_name(ha_url, token, target_name):
    """Find the entity ID matching the friendly name"""
    try:
        url = f"{ha_url}/api/states"
        r = requests.get(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=10)
        if r.status_code == 200:
            states = r.json()
            # Normalize target
            t_low = target_name.lower()
            
            # 1. Exact Friendly Name Match
            for s in states:
                if s['entity_id'].startswith("media_player."):
                    fq = s.get('attributes', {}).get('friendly_name', '').lower()
                    if fq == t_low:
                         return s['entity_id']
            
            # 2. Fuzzy/Partial Match
            for s in states:
                 if s['entity_id'].startswith("media_player."):
                    fq = s.get('attributes', {}).get('friendly_name', '').lower()
                    if t_low in fq or fq in t_low:
                         return s['entity_id']
                         
    except Exception as e:
        print(f"DEBUG: Error resolving entity: {e}")
    return None

def main():
    print(f"--- Starting Live Roku Test on '{DEVICE_NAME}' ---")
    print(f"Target API: {API_URL}")
    
    # Setup
    config = load_env_vars()
    ha_url = config.get("HA_URL")
    ha_token = config.get("HA_TOKEN")
    
    if not ha_url or not ha_token:
        print("ERROR: Could not load HA_URL or HA_TOKEN from .env")
        sys.exit(1)

    logger = lambda n, s, m: print(f"[{n}] {s}: {m}")
    tester = BaseTest(API_URL, {"Content-Type": "application/json"}, logger)
    
    # 1. Resolve Entity Dynamically
    print(f"Resolving entity for '{DEVICE_NAME}'...")
    entity_id = resolve_entity_by_name(ha_url, ha_token, DEVICE_NAME)
    
    if not entity_id:
        print(f"ERROR: Could not find any media_player matching '{DEVICE_NAME}' in Home Assistant.")
        print("Please check the name or the HA connection.")
        sys.exit(1)
        
    print(f"DEBUG: Resolved '{DEVICE_NAME}' -> {entity_id}")
    
    # 2. Check Initial State
    initial_state = get_ha_state(ha_url, ha_token, entity_id)
    print(f"Initial State: {initial_state}")
    
    # 3. Send Play Command
    query = sys.argv[1] if len(sys.argv) > 1 else f"Play Brandon Lake on {DEVICE_NAME}"
    print(f"\nSending Query: '{query}'...")
    
    payload = {
        "query": query
    }
    # Add user if explicitly needed for history, but default to admin
    payload["user"] = os.getenv("RAG_TEST_USER", "admin")
    
    # POST to /api/chat
    response_text, status = tester.safe_post("/api/chat", payload, "Roku Play Command")
    print(f"API Response ({status}): {response_text[:200]}...")
    
    if status != 200:
        print("TEST FAILED: API returned non-200 status")
        sys.exit(1)
        
    # 4. Verify State Change (Poll for up to 30s)
    print("\nVerifying State Change (Waiting for 'playing' or 'buffering')...")
    start_time = time.time()
    success = False
    
    while time.time() - start_time < 30:
        current_state = get_ha_state(ha_url, ha_token, entity_id)
        print(f"[{int(time.time()-start_time)}s] State: {current_state}")
        
        if current_state in ["playing", "buffering"]:
            print(f"\nSUCCESS! Device entered state: {current_state}")
            success = True
            break
        
        time.sleep(2)
        
    if not success:
        print(f"\nTEST FAILED: Device did not enter playing state. Final state: {get_ha_state(ha_url, ha_token, entity_id)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
