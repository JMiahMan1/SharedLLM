"""
Test Helper Functions for State Verification
NO HTTP 200 CHECKS - Only real state verification
"""
import requests
import time
import os
from dotenv import load_dotenv

# Load .env file FIRST
load_dotenv()

HA_URL = os.getenv("HA_URL", "https://localhost:8123")
HA_TOKEN = os.getenv("HA_TOKEN", "")

if not HA_TOKEN:
    print("[WARN] HA_TOKEN not set - state verification will fail!")

HA_HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json"
}

def get_ha_state(entity_id):
    """Get current state of Home Assistant entity"""
    try:
        r = requests.get(
            f"{HA_URL}/api/states/{entity_id}", 
            headers=HA_HEADERS, 
            timeout=5
        )
        if r.status_code == 200:
            return r.json()
        print(f"[WARN] Failed to get state for {entity_id}: {r.status_code}")
        return None
    except Exception as e:
        print(f"[ERROR] Exception getting state for {entity_id}: {e}")
        return None

def verify_device_state(entity_id, expected_state, timeout=10):
    """
    Verify device reaches expected state within timeout.
    Returns (success, actual_state)
    """
    start = time.time()
    last_state = None
    
    while time.time() - start < timeout:
        state_data = get_ha_state(entity_id)
        if state_data:
            last_state = state_data.get('state')
            if last_state == expected_state:
                return (True, last_state)
        time.sleep(1)
    
    return (False, last_state)

def verify_media_playing(entity_id, timeout=10):
    """Verify media is actually playing"""
    success, state = verify_device_state(entity_id, 'playing', timeout)
    return success

def verify_device_on(entity_id, timeout=10):
    """Verify device is powered on"""
    state_data = get_ha_state(entity_id)
    if not state_data:
        return False
    
    current_state = state_data.get('state')
    # Consider 'on', 'playing', 'paused', 'idle' as "on"
    return current_state not in ['off', 'unavailable', 'unknown']

def verify_app_running(entity_id, app_name, timeout=10):
    """
    Verify specific app is running.
    Checks app_id, app_name, or media_title attributes.
    """
    start = time.time()
    app_name_lower = app_name.lower()
    
    while time.time() - start < timeout:
        state_data = get_ha_state(entity_id)
        if state_data:
            attrs = state_data.get('attributes', {})
            
            # Check various possible attribute names
            app_id = str(attrs.get('app_id', '')).lower()
            app_name_attr = str(attrs.get('app_name', '')).lower()
            media_title = str(attrs.get('media_title', '')).lower()
            
            if (app_name_lower in app_id or 
                app_name_lower in app_name_attr or 
                app_name_lower in media_title):
                return True
        
        time.sleep(1)
    
    return False

def get_timer_list(api_url, headers):
    """Get list of timers from API"""
    try:
        r = requests.get(f"{api_url}/api/timer/list", headers=headers, timeout=5)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        print(f"[ERROR] Failed to get timer list: {e}")
        return None

def verify_timer_exists(api_url, headers, timer_name):
    """Verify timer with given name exists"""
    timers = get_timer_list(api_url, headers)
    if not timers:
        return False
    
    # timers might be a list or dict
    if isinstance(timers, list):
        return any(t.get('summary', '').lower() == timer_name.lower() for t in timers)
    elif isinstance(timers, dict):
        timer_list = timers.get('timers', [])
        return any(t.get('summary', '').lower() == timer_name.lower() for t in timer_list)
    
    return False

def get_note_list(api_url, headers):
    """Get list of notes from API"""
    try:
        r = requests.get(f"{api_url}/api/notes/list", headers=headers, timeout=5)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        print(f"[ERROR] Failed to get note list: {e}")
        return None

def verify_note_exists(api_url, headers, note_content):
    """Verify note with given content exists"""
    notes = get_note_list(api_url, headers)
    if not notes:
        return False
    
    if isinstance(notes, list):
        return any(note_content.lower() in str(n.get('content', '')).lower() for n in notes)
    elif isinstance(notes, dict):
        note_list = notes.get('notes', [])
        return any(note_content.lower() in str(n.get('content', '')).lower() for n in note_list)
    
    return False
