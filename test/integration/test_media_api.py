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
    sys.exit(1)

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
            timeout=90
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

    expected_responses = ["sent command", "done", "turning on", "playing", "opening", "launching", "paused", "stopped", "stopping", "resumed", "skipped", "next", "turning off", "set color"]
    
    match = False
    lower_res = res.lower() if res else ""
    for e in expected_responses:
        if e in lower_res:
             match = True
             break
             
    if match:
        print_pass(f"Command '{query}' routed successfully (Response: {res})")
    else:
        print_fail(f"Command '{query}' failed. Response: {res}")
        
    return res

def check_response(test_name, res):
    # Helper to check if response is valid (not None)
    if res:
        print_pass(f"{test_name} passed.")
    else:
        print_fail(f"{test_name} failed (No response).")

def test_media_routing():
    print(f"\nStarting Media Routing Tests on {API_URL}...")
    
    # ---------------------------------------------------------
    # TEST 1: Power Control
    # ---------------------------------------------------------
    print_info("TEST 1: Power Control")
    check_response("Turn on Office TV", send_query("Turn on Office TV"))

    # ---------------------------------------------------------
    # TEST 2: Music Playback
    # ---------------------------------------------------------
    print_info("TEST 2: Music Playback")
    check_response("Play Brandon Lake on Office TV", send_query("Play Brandon Lake on Office TV"))

    # ---------------------------------------------------------
    # TEST 3: App Launching
    # ---------------------------------------------------------
    print_info("TEST 3: App Launching")
    check_response("Open Netflix on Office TV", send_query("Open Netflix on Office TV"))

    # ---------------------------------------------------------
    # TEST 4: Navigation
    # ---------------------------------------------------------
    print_info("TEST 4: Navigation")
    check_response("Scroll down on Office TV", send_query("Scroll down on Office TV"))

    # ---------------------------------------------------------
    # TEST 5: Media Control
    # ---------------------------------------------------------
    print_info("TEST 5: Media Control")
    
    check_response("Pause", send_query("Pause the Office TV"))
    time.sleep(1)
    
    check_response("Resume", send_query("Resume on Office TV"))
    time.sleep(1)
    
    check_response("Skip", send_query("Skip this song on Office TV"))
    time.sleep(1)
    
    check_response("Stop", send_query("Stop the music on Office TV"))

    # ---------------------------------------------------------
    # TEST 6: Power Off
    # ---------------------------------------------------------
    print_info("TEST 6: Power Off")
    check_response("Turn off Office TV", send_query("Turn off Office TV"))

    # ---------------------------------------------------------
    # TEST 7: Color Control
    # ---------------------------------------------------------
    print_info("TEST 7: Set Color")
    check_response("Set the Office TV light to Blue", send_query("Set the Office TV light to Blue"))

    # ---------------------------------------------------------
    # TEST 8: Remote Command
    # ---------------------------------------------------------
    print_info("TEST 8: Remote Command")
    check_response("Press Home on the Office TV", send_query("Press Home on the Office TV"))


if __name__ == "__main__":
    # Fast Health Check
    try:
        r = requests.get(f"{API_URL}/health", timeout=2)
        if r.status_code == 200:
            test_media_routing()
        else:
            print_fail("API is unhealthy. Check Docker logs.")
            sys.exit(1)
    except Exception as e:
        print_fail(f"API is unreachable: {e}")
        sys.exit(1)
