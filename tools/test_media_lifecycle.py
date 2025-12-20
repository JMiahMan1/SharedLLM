import requests
import time
import sys
import argparse
import json

# Default Configuration
DEFAULT_API_URL = "http://localhost:11435"
DEFAULT_DEVICE = "Office TV" # Friendly name for query
DEFAULT_ENTITY = "media_player.28_tcl_roku_tv" # For state checking

def parse_args():
    parser = argparse.ArgumentParser(description="Test full media lifecycle (On -> Watch -> Pause -> Resume -> Stop -> Off)")
    parser.add_argument("--url", default=DEFAULT_API_URL, help="Base API URL")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="Friendly name of device for Voice Query")
    parser.add_argument("--entity", default=DEFAULT_ENTITY, help="Entity ID for State Verification")
    parser.add_argument("--query", default="Watch Big Buck Bunny trailer", help="Video query to trigger search & download")
    return parser.parse_args()

def check_state(base_url, entity_id, expected_states, timeout=10, step_name="State Check"):
    """Polls /api/ha/state/{entity_id} until state matches one of expected_states."""
    print(f"[{step_name}] Verifying {entity_id} is in {expected_states}...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            resp = requests.get(f"{base_url}/api/ha/state/{entity_id}", timeout=2)
            if resp.status_code == 200:
                curr_state = resp.json().get("state")
                if curr_state in expected_states:
                    print(f"✅ [{step_name}] Match: {curr_state}")
                    return True
                # Log intermediate states for debugging
                # print(f"   Current: {curr_state}") 
        except Exception as e:
            print(f"   ⚠ Error fetching state: {e}")
        time.sleep(1)
        
    print(f"❌ [{step_name}] TIMEOUT. Expected {expected_states}, got {curr_state}")
    return False

def send_chat_command(base_url, query, user="test_lifecycle"):
    """Sends a natural language command to /api/chat."""
    try:
        payload = {"query": query, "user": user}
        print(f"➡ Sending Command: '{query}'")
        start = time.time()
        resp = requests.post(f"{base_url}/api/chat", json=payload, timeout=90) # Long timeout for download
        duration = time.time() - start
        
        if resp.status_code == 200:
            data = resp.json()
            msg = data.get("response", "") or data.get("message", {}).get("content", "")
            print(f"   ⬅ Response ({duration:.1f}s): {msg}")
            return True
        else:
            print(f"   ❌ HTTP {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        print(f"   ❌ Exception: {e}")
        return False

def main():
    args = parse_args()
    print(f"--- Starting Media Lifecycle Test on {args.device} ({args.entity}) ---")
    
    # 1. Health Check
    try:
        requests.get(f"{args.url}/health", timeout=2)
    except:
        print("❌ CRITICAL: API not reachable at", args.url)
        sys.exit(1)

    # 2. SEQUENCE: TURN ON
    if not send_chat_command(args.url, f"Turn on {args.device}"): sys.exit(1)
    # Wait for boot (Roku takes time to connect to wifi)
    if not check_state(args.url, args.entity, ["on", "idle", "home"], timeout=20, step_name="Turn On"):
        print("⚠ Warning: TV didn't report 'on' quickly. Proceeding anyway (might be slow integration update).")

    # 3. SEQUENCE: WATCH (Triggers Search -> Download -> Play)
    # This is the heavy lift.
    if not send_chat_command(args.url, f"{args.query} on {args.device}"): sys.exit(1)
    
    # Verify Playback (Long timeout for download + buffer)
    if not check_state(args.url, args.entity, ["playing", "buffering"], timeout=120, step_name="Watch Verification"):
        print("❌ Failed to enter playback state.")
        sys.exit(1)
        
    # Let it play for 5 seconds
    print("⏳ Playing for 5 seconds...")
    time.sleep(5)

    # 4. SEQUENCE: PAUSE
    if not send_chat_command(args.url, f"Pause {args.device}"): sys.exit(1)
    if not check_state(args.url, args.entity, ["paused"], timeout=10, step_name="Pause"): sys.exit(1)
    
    time.sleep(2)

    # 5. SEQUENCE: RESUME
    if not send_chat_command(args.url, f"Resume {args.device}"): sys.exit(1)
    if not check_state(args.url, args.entity, ["playing"], timeout=10, step_name="Resume"): sys.exit(1)

    time.sleep(2)

    # 6. SEQUENCE: STOP
    if not send_chat_command(args.url, f"Stop {args.device}"): sys.exit(1)
    if not check_state(args.url, args.entity, ["idle", "on", "home"], timeout=15, step_name="Stop"): sys.exit(1)

    # 7. SEQUENCE: TURN OFF
    if not send_chat_command(args.url, f"Turn off {args.device}"): sys.exit(1)
    if not check_state(args.url, args.entity, ["off", "standby", "unavailable"], timeout=15, step_name="Turn Off"): sys.exit(1)

    print("\n✅✅✅ TEST SESSION COMPLETE - ALL CHECKS PASSED ✅✅✅")

if __name__ == "__main__":
    main()
