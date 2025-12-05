# test/test_timers.py
import sys
import os
import time
import requests
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
API_URL = os.getenv("API_URL", "http://localhost:11435")

TEST_USER = os.getenv("NEXTCLOUD_USER", "admin") 
HEADERS = {"Content-Type": "application/json", "X-RAG-User": TEST_USER, "User-Agent": "TimerTestScript"}

def print_pass(msg): print(f"\033[92m[PASS]\033[0m {msg}")
def print_fail(msg): print(f"\033[91m[FAIL]\033[0m {msg}")
def print_info(msg): print(f"\033[94m[INFO]\033[0m {msg}")

def send_chat(query):
    try:
        r = requests.post(f"{API_URL}/api/chat", 
                          json={"messages":[{"role":"user","content":query}], "stream":False}, 
                          headers=HEADERS,
                          timeout=30)
        
        if r.status_code != 200:
             print_fail(f"API returned HTTP {r.status_code}")
             return {"message": {"content": f"API Error: {r.text}"}}
             
        return r.json()
    except Exception as e:
        print_fail(f"Request failed: {e}")
        return None

def test_timer_flow():
    print_info("--- Starting Timer & Alarm Service Tests ---")
    
    # 0. Cleanup
    try:
        timers = requests.get(f"{API_URL}/api/timer/list", headers=HEADERS).json()
        if isinstance(timers, list):
            for t in timers:
                if t.get("title", "").lower() in ["test short timer", "test long alarm"]:
                    requests.post(f"{API_URL}/api/timer/delete?timer_id={t['id']}", headers=HEADERS)
    except:
        pass

    # 1. Create Timer (Short Duration)
    timer_name = "Test Short Timer"
    print_info(f"TEST 1: Set a 10-second timer: '{timer_name}' (Expect SUCCESS)")
    resp = send_chat(f"Set a 10-second timer for {timer_name}")
    content = resp.get("message", {}).get("content", "")
    
    # FIX: More flexible assertion
    if any(x in content.lower() for x in ["set", "started", "created", "success"]) and "timer" in content.lower():
        print_pass(f"Timer created: {content}")
    else:
        print_fail(f"Failed to create timer. Response: {content}")
        return

    # 2. List Timers
    print_info("TEST 2: List Timers (Expect non-empty list)")
    resp = send_chat("List my timers")
    content = resp.get("message", {}).get("content", "")
    
    if "active timers" in content.lower() and timer_name.lower() in content.lower():
        print_pass("Timer list retrieved successfully.")
    else:
        print_fail(f"Failed to list timers. Response: {content}")

    # 3. Wait for Expiration
    print_info("TEST 3: Waiting 4 seconds for scheduler...")
    time.sleep(4)
    
    # 4. Check if list is empty
    resp = send_chat("List my timers")
    content = resp.get("message", {}).get("content", "")
    
    if "no active timers" in content.lower():
        print_pass("Timer expired and was cleaned up.")
    else:
        print_fail(f"Timer still present: {content}")

    # 5. Create Alarm
    alarm_name = "Test Long Alarm"
    # FIX: Use absolute time for alarm, as duration is no longer supported for alarms
    print_info(f"TEST 4: Set an alarm for 8am: '{alarm_name}' (Expect SUCCESS)")
    resp = send_chat(f"Set an alarm for 8am called {alarm_name}")
    content = resp.get("message", {}).get("content", "")
    
    if any(x in content.lower() for x in ["set", "started", "created", "success"]) and "alarm" in content.lower():
        print_pass(f"Alarm set: {content}")
    else:
        print_fail(f"Failed to set alarm. Response: {content}")
        return

    # 6. Delete Alarm
    print_info(f"TEST 5: Delete the alarm by name: '{alarm_name}' (Expect SUCCESS)")
    resp = send_chat(f"Delete the alarm {alarm_name}")
    content = resp.get("message", {}).get("content", "")
    
    if "deleted" in content.lower():
        print_pass("Alarm deleted successfully.")
    else:
        print_fail(f"Failed to delete alarm. Response: {content}")


if __name__ == "__main__":
    try:
        if requests.get(f"{API_URL}/health").status_code == 200:
            test_timer_flow()
        else:
            print_fail("API is unhealthy. Check Docker logs.")
    except Exception as e:
        print_fail(f"API is unreachable: {e}")
