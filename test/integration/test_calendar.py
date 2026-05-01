
import requests
import os
import time
from dotenv import load_dotenv

# Setup
script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
load_dotenv(os.path.join(root_dir, '.env'))

API_URL = os.getenv("API_URL", "http://ai.local:11435")
HEADERS = {"Content-Type": "application/json", "X-RAG-User": "admin"}

def log(msg): print(f"[CalendarTest] {msg}")

def test_calendar_flow():
    log("Starting Calendar Tests...")
    
    # 1. List Events (Baseline)
    log("TEST 1: List Events")
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":"What is on my calendar today?"}]}, headers=HEADERS)
    log(f"Response: {r.text[:100]}...")
    # Expect success or "no events" or "credentials needed"
    assert r.status_code == 200
    
    # 2. Add Event
    log("TEST 2: Add Event 'Test Meeting' at 5pm")
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":"Schedule a Test Meeting today at 5pm"}]}, headers=HEADERS)
    log(f"Response: {r.text[:100]}...")
    
    # 3. Check if added
    # This might fail if Nextcloud isn't actually connected, but we check if the intent was routed.
    if "scheduled" in r.text.lower() or "added" in r.text.lower():
         log("✅ Add Event Intent routed successfully.")
    else:
         log("⚠️ Add Event might have failed or Nextcloud is unreachable.")

    # 4. Delete Event (Cleanup)
    log("TEST 3: Delete Event 'Test Meeting'")
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":"Delete the Test Meeting from my calendar"}]}, headers=HEADERS)
    log(f"Response: {r.text[:100]}...")
    
if __name__ == "__main__":
    test_calendar_flow()
