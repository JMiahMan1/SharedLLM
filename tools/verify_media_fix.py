import requests
import time
import re
import sys

BASE_URL = "http://192.168.2.211:11435"
HEADERS = {"X-RAG-User": "admin"}

def get_logs(lines=50):
    try:
        resp = requests.get(f"{BASE_URL}/api/admin/logs?lines={lines}", headers=HEADERS, timeout=5)
        if resp.status_code == 200:
            return resp.json().get("logs", [])
    except Exception as e:
        print(f"  [WARN] Failed to fetch logs: {e}")
    return []

def verify_log_content(pre_logs, expected_pattern):
    """Check if new logs contain the expected pattern."""
    post_logs = get_logs(100)
    # Filter only new lines
    new_lines = [l for l in post_logs if l not in pre_logs]
    
    for line in new_lines:
        if re.search(expected_pattern, line, re.IGNORECASE):
            return True, line.strip()
    return False, None

def run_test(command, description, expected_log_pattern=None):
    print(f"\n--- Testing: {description} ---")
    print(f"Command: '{command}'")
    
    pre_logs = get_logs(100)
    
    try:
        start_time = time.time()
        resp = requests.post(
            f"{BASE_URL}/api/chat", 
            json={"query": command}, 
            headers=HEADERS, 
            timeout=30
        )
        duration = time.time() - start_time
        
        if resp.status_code == 200:
            data = resp.json()
            response_text = data.get("message", {}).get("content", "") or data.get("response", "")
            print(f"  [API OK] ({duration:.2f}s) Response: {response_text[:100]}...")
            
            if expected_log_pattern:
                print(f"  [VERIFY] Checking logs for pattern: '{expected_log_pattern}'...")
                # Retrying log check for a few seconds as logs might be async
                found = False
                for _ in range(5):
                    found, line = verify_log_content(pre_logs, expected_log_pattern)
                    if found:
                        print(f"  [PASS] Log Confirmation: {line}")
                        return True
                    time.sleep(1)
                
                print(f"  [FAIL] Log pattern not found in recent logs.")
                print("  Recent logs:")
                for l in get_logs(20)[-5:]:
                    print(f"    {l.strip()}")
                return False
            return True
            
        else:
            print(f"  [FAIL] API Error {resp.status_code}: {resp.text}")
            return False
            
    except Exception as e:
        print(f"  [ERROR] Exception during test: {e}")
        return False

def main():
    print(f"Target: {BASE_URL}")
    
    # 1. Music Assistant Tests
    # Note: "Sent command to play_media" is the success message from execute_ha_service
    # Or "Music Assistant delegation succeeded" from commands.py
    
    if not run_test(
        "Play Brandon Lake on Office TV", 
        "Music Assistant - Office TV",
        r"(Music Assistant delegation succeeded|Sent command to play_media)"
    ):
        print("Test 1 Failed. Continuing...")

    time.sleep(5)
    
    if not run_test(
        "Stop Office TV", 
        "Stop Music",
        r"(Sent command to media stop|media_stop|SUCCESS)"
    ):
        print("Test 2 Failed.")
        
    # 2. Video Tests (YouTube)
    # Pattern: "intent='watch_video'" or "Sent command to play_media" with video
    
    time.sleep(2)
    if not run_test(
        "Play Big Buck Bunny video on Master Bedroom TV", 
        "YouTube Video - Master Bedroom",
        r"(Sent command to play_media|SUCCESS)"
    ):
        print("Test 3 Failed.")
        
    time.sleep(10) # Let it play
    
    if not run_test(
        "Pause Master Bedroom TV", 
        "Pause Video",
        r"(Sent command to media pause|SUCCESS)"
    ):
        print("Test 4 Failed.")
        
    # 3. Gracies TV
    time.sleep(2)
    if not run_test(
        "Play generic music on Gracies TV", 
        "Music - Gracies TV",
        r"(Music Assistant delegation succeeded|Sent command to play_media)"
    ):
        print("Test 5 Failed.")
        
    time.sleep(2)
    run_test("Stop Gracies TV", "Stop Gracie")

    print("\nVerification Run Complete.")

if __name__ == "__main__":
    main()
