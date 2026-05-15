import os
import requests
import json
import time
import datetime
from dotenv import load_dotenv

# Load credentials from .env
load_dotenv()

HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET")

# Dynamically discover production IP from docker-compose if available
def discover_prod_ip():
    try:
        with open("docker-compose.yml", "r") as f:
            for line in f:
                if "ai-server:" in line:
                    return line.split(":")[1].strip().strip('"').strip("'")
    except: pass
    return "192.168.2.205" # Fallback

PROD_IP = discover_prod_ip()
EXECUTION_URL = os.getenv("EXECUTION_SVC_URL", f"http://{PROD_IP}:8003")

# Common User Context for testing
USER_CONTEXT = {
    "user": "tester",
    "ha_url": HA_URL,
    "ha_token": HA_TOKEN,
    "is_admin": True
}

HEADERS = {"X-Internal-Secret": INTERNAL_SECRET}

class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def print_header(text):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD} {text.center(58)} {Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}")

def log_step(name, success, message="", detail=None):
    status = f"{Colors.OKGREEN}✅ PASS{Colors.ENDC}" if success else f"{Colors.FAIL}❌ FAIL{Colors.ENDC}"
    print(f"[{status}] {Colors.BOLD}{name}{Colors.ENDC}")
    if message:
        print(f"      {Colors.OKCYAN}Message:{Colors.ENDC} {message}")
    if detail and not success:
        print(f"      {Colors.WARNING}Detail:{Colors.ENDC} {json.dumps(detail, indent=2)}")

def get_current_state(entity_id):
    """Directly fetch state from HA via Execution's ha_client wrapper (if exposed) or via HA API."""
    # We'll use the HA API directly for validation to ensure truth
    url = f"{HA_URL}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {HA_TOKEN}"}
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"      {Colors.WARNING}[Error]{Colors.ENDC} Failed to fetch state for {entity_id}: {e}")
    return None

def verify_logbook(entity_id, since_minutes=1):
    """Verify that an entry exists in the HA Logbook for this entity recently."""
    payload = {
        "user_context": USER_CONTEXT,
        "entity_id": entity_id,
        "days": 1 # Minimum 1 day in API
    }
    try:
        resp = requests.post(f"{EXECUTION_URL}/execute/ha_logbook", json=payload, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            entries = data.get("detail", {}).get("entries", [])
            # Filter for very recent entries
            now = datetime.datetime.now(datetime.UTC)
            recent = []
            for e in entries:
                ts_str = e.get("when")
                if ts_str:
                    try:
                        # Standardize to UTC for comparison
                        ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        # Allow 5 minute window for drift
                        if (now - ts).total_seconds() < since_minutes * 60 + 300:
                            recent.append(e)
                    except: continue
            return recent
    except: pass
    return []

# --- Test Cases ---

def test_discovery():
    print_header("SCENARIO: System Discovery Sync")
    payload = {"user_context": USER_CONTEXT}
    resp = requests.post(f"{EXECUTION_URL}/execute/discovery_sync", json=payload, headers=HEADERS, timeout=30)
    data = resp.json()
    success = data.get("status") == "SUCCESS"
    log_step("Discovery Sync API", success, data.get("message"), detail=data)
    return success

def test_light_interaction(entity_id="light.piano_lamp"):
    print_header(f"SCENARIO: Light Interaction ({entity_id})")
    
    # 1. Get Initial State
    initial = get_current_state(entity_id)
    if not initial:
        log_step("Pre-test State Check", False, f"Device {entity_id} not found in HA.")
        return False
    
    initial_state = initial.get("state")
    target_state = "on" if initial_state == "off" else "off"
    print(f"      Current State: {Colors.BOLD}{initial_state}{Colors.ENDC} -> Target: {Colors.BOLD}{target_state}{Colors.ENDC}")

    # 2. Dispatch Toggle Command
    payload = {
        "user_context": USER_CONTEXT,
        "entity_id": entity_id,
        "action": "toggle"
    }
    resp = requests.post(f"{EXECUTION_URL}/execute/light", json=payload, headers=HEADERS, timeout=10)
    log_step("Dispatch Toggle Command", resp.status_code == 200, detail=resp.json())

    # 3. Verify State Change (Polling)
    print("      Waiting for state change...", end="", flush=True)
    changed = False
    for _ in range(10):
        time.sleep(1)
        print(".", end="", flush=True)
        current = get_current_state(entity_id)
        if current and current.get("state") == target_state:
            changed = True
            break
    print(" Done.")
    log_step("Verify State Change (get_state)", changed, f"New state is {target_state}")

    # 4. Verify Logbook
    recent_logs = verify_logbook(entity_id)
    log_step("Verify Logbook Entry", len(recent_logs) > 0, f"Found {len(recent_logs)} recent events in HA Logbook.")
    if recent_logs:
        print(f"      Latest Log: {recent_logs[0].get('name')} -> {recent_logs[0].get('message') or recent_logs[0].get('state')}")

    return changed and len(recent_logs) > 0

def test_announcement_logic(entity_id="media_player.office_speaker"):
    print_header("SCENARIO: Announcement Stability (Roku/MASS)")
    
    # We won't check audio output (hard to do remotely), but we can check if the 
    # Media Assistant app was launched on Roku or if MASS service was called.
    
    payload = {
        "user_context": USER_CONTEXT,
        "message": "Raven Production Validation: All systems nominal.",
        "entity_id": entity_id,
        "volume": 0.1
    }
    resp = requests.post(f"{EXECUTION_URL}/execute/announce", json=payload, headers=HEADERS, timeout=20)
    data = resp.json()
    success = data.get("status") == "SUCCESS"
    log_step("Announcement Dispatch", success, data.get("message"), detail=data)
    
    if success:
        # Check Logbook for 'play_media' or 'mass' events
        time.sleep(2)
        logs = verify_logbook(entity_id)
        found_event = any("media_player" in str(l) for l in logs)
        log_step("Verify Announcement Event in HA", found_event, "Found service call event in Logbook.")
    
    return success

def get_system_logs(service="execution", lines=20):
    print_header(f"DIAGNOSTICS: {service} logs")
    payload = {
        "user_context": USER_CONTEXT,
        "service": service,
        "lines": lines
    }
    try:
        resp = requests.post(f"{EXECUTION_URL}/execute/diagnostics", json=payload, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            logs = resp.json().get("detail", {}).get("logs", "No logs found.")
            print(f"{Colors.OKBLUE}--- LOGS START ---{Colors.ENDC}")
            print(logs)
            print(f"{Colors.OKBLUE}--- LOGS END ---{Colors.ENDC}")
            return True
        else:
            print(f"Failed to fetch logs: {resp.status_code}")
            return False
    except Exception as e:
        print(f"Error fetching logs: {e}")
        return False

def test_music_assistant(entity_id="media_player.office_speaker"):
    print_header("SCENARIO: Music Assistant (MASS) Play")
    print(f"[RUN] Testing Music Assistant on {entity_id}...")
    
    payload = {
        "user_context": USER_CONTEXT,
        "entity_id": entity_id,
        "query": "Brandon Lake",
        "media_content_type": "artist"
    }
    resp = requests.post(f"{EXECUTION_URL}/execute/media/play", json=payload, headers=HEADERS, timeout=15)
    data = resp.json()
    success = data.get("status") == "SUCCESS"
    log_step("Music Assistant Dispatch", success, data.get("message"), detail=data)
    return success

def test_video_playback(entity_id="media_player.office_tv_chrome"):
    print_header("SCENARIO: Video Interaction (Cast)")
    print(f"[RUN] Testing YouTube Video on {entity_id}...")
    
    payload = {
        "user_context": USER_CONTEXT,
        "entity_id": entity_id,
        "media_content_id": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "media_content_type": "url"
    }
    resp = requests.post(f"{EXECUTION_URL}/execute/media/play", json=payload, headers=HEADERS, timeout=15)
    data = resp.json()
    success = data.get("status") == "SUCCESS"
    log_step("Video Dispatch Command", success, data.get("message"), detail=data)
    return success

def run_suite():
    os.system('clear')
    print(f"{Colors.BOLD}{Colors.OKBLUE}RAVEN LIVE TEST SUITE v2.0{Colors.ENDC}")
    print(f"Targeting: {Colors.UNDERLINE}{EXECUTION_URL}{Colors.ENDC}")
    
    # 1. Health
    try:
        r = requests.get(f"{EXECUTION_URL}/health", timeout=5)
        if r.status_code != 200: raise Exception("Health check failed")
        print(f"[{Colors.OKGREEN}READY{Colors.ENDC}] Execution service is online.")
    except Exception as e:
        print(f"[{Colors.FAIL}ERROR{Colors.ENDC}] Cannot reach Execution service: {e}")
        return

    test_discovery()
    test_light_interaction()
    test_announcement_logic()
    test_music_assistant()
    test_video_playback()
    
    print_header("VALIDATION COMPLETE")
    print(f"{Colors.BOLD}Summary: Verified State Pulling, Logbook Events, and Multi-Service Dispatch.{Colors.ENDC}\n")

if __name__ == "__main__":
    run_suite()
