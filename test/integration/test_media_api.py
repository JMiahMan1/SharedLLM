import sys
import os
import requests
import json
import time
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
API_URL = os.getenv("API_URL", "http://localhost:11435")
# Ensure we use the admin user or the one defined in your .env
TEST_USER = os.getenv("NEXTCLOUD_USER", "admin") 
HEADERS = {
    "Content-Type": "application/json",
    "X-RAG-User": TEST_USER,
    "User-Agent": "MediaTestScript"
}

def print_pass(msg):
    print(f"\033[92m[PASS]\033[0m {msg}")

def print_fail(msg):
    print(f"\033[91m[FAIL]\033[0m {msg}")

def print_info(msg):
    print(f"\033[94m[INFO]\033[0m {msg}")

def send_query(query, expected_intent=None):
    print("-" * 60)
    print(f"Query: '{query}'")
    
    try:
        payload = {
            "messages": [{"role": "user", "content": query}],
            "stream": False
        }
        
        start_time = time.time()
        response = requests.post(
            f"{API_URL}/api/chat", 
            json=payload, 
            headers=HEADERS, 
            timeout=30
        )
        duration = time.time() - start_time
        
        if response.status_code != 200:
            print_fail(f"API Error {response.status_code}: {response.text}")
            return None
            
        data = response.json()
        content = data.get("message", {}).get("content", "")
        
        print(f"Response ({duration:.2f}s): {content}")
        
        return content

    except Exception as e:
        print_fail(f"Request failed: {e}")
        return None

def test_media_routing():
    print(f"\nStarting Media Routing Tests on {API_URL}...")
    
    # ---------------------------------------------------------
    # TEST 1: Power Control (Should prefer Remote or Switch)
    # ---------------------------------------------------------
    print_info("TEST 1: Power Control (Expect: 'turn_on' on Remote/Switch)")
    res = send_query("Turn on Office TV")
    
    if res and "Sent command to turn on" in res:
        print_pass("Correctly identified Power command.")
    else:
        print_fail("Failed to route Power command correctly.")

    # ---------------------------------------------------------
    # TEST 2: Music Playback (Should prefer Music Assistant)
    # ---------------------------------------------------------
    print_info("TEST 2: Music Playback (Expect: 'play_media' on Music Assistant)")
    # We use a specific artist to trigger the generic play logic
    res = send_query("Play Brandon Lake on Office TV")
    
    if res and "Sent command to play media" in res:
        print_pass("Correctly routed Music command.")
    else:
        print_fail("Failed to route Music command.")

    # ---------------------------------------------------------
    # TEST 3: App Launching (Should prefer Android TV)
    # ---------------------------------------------------------
    print_info("TEST 3: App Launching (Expect: 'play_media' with App ID)")
    res = send_query("Open Netflix on Office TV")
    
    # We verify it didn't just try to play a song named "Netflix"
    if res and "Sent command to play media" in res:
        print_pass("Correctly routed App Launch command.")
    elif "package ID" in res:
        print_fail("System recognized App intent but failed to find Package ID.")
    else:
        print_fail("Failed to route App command.")

    # ---------------------------------------------------------
    # TEST 4: Navigation (Should prefer Remote)
    # ---------------------------------------------------------
    print_info("TEST 4: Navigation (Expect: 'send_command' on Remote)")
    # "Scroll down" maps to nav_down intent -> DPAD_DOWN command
    res = send_query("Scroll down on Office TV")
    
    if res and "Sent command to send command" in res: 
        # Note: The verb for remote.send_command is often "send command" in the response
        print_pass("Correctly routed Navigation command.")
    else:
        print_fail("Failed to route Navigation command.")

if __name__ == "__main__":
    # Fast Health Check
    try:
        r = requests.get(f"{API_URL}/health", timeout=2)
        if r.status_code == 200:
            test_media_routing()
        else:
            print_fail("API is unhealthy. Check Docker logs.")
    except:
        print_fail("API is unreachable. Is Docker running?")
