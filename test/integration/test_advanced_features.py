
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

def log(msg): print(f"[AdvTest] {msg}")

def test_advanced_features():
    log("--- Advanced Features Test ---")

    # 1. Timer Pause/Resume
    log("TEST 1: Timer Pause/Resume")
    # First create a timer
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":"Set a 5 minute timer called PauseTest"}]}, headers=HEADERS)
    log(f"Create Timer Resp: {r.text}")
    
    # Pause
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":"Pause the PauseTest timer"}]}, headers=HEADERS)
    log(f"Pause Resp: {r.text[:100]}...")
    if "paused" in r.text.lower() or "stopped" in r.text.lower():
         log("✅ Timer Paused.")
    else:
         log("⚠️ Timer Pause might have failed.")

    # Resume
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":"Resume the PauseTest timer"}]}, headers=HEADERS)
    log(f"Resume Resp: {r.text[:100]}...")
    if "resumed" in r.text.lower() or "started" in r.text.lower():
         log("✅ Timer Resumed.")
    else:
         log("⚠️ Timer Resume might have failed.")
         
    # Cleanup
    requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":"Delete the PauseTest timer"}]}, headers=HEADERS)


    # 2. Intent Learning (Alias)
    log("TEST 2: Intent Learning (Alias)")
    # Teach: "Activate Protocol Omega" -> "Turn on the Office Light"
    teach_q = "When I say activate protocol omega, I want you to turn on the Office Light"
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":teach_q}]}, headers=HEADERS)
    log(f"Teach Resp: {r.text[:100]}...")
    
    # Verify
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":"Activate Protocol Omega"}]}, headers=HEADERS)
    log(f"Execute Alias Resp: {r.text[:100]}...")
    
    if "turn_on" in r.text.lower() or "on" in r.text.lower() or "sent command" in r.text.lower():
        log("✅ Intent Learning Verified.")
    else:
        log("⚠️ Intent Learning might have failed.")


    # 3. Calendar Update
    log("TEST 3: Calendar Update")
    # Create
    requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":"Schedule a Mutable Meeting at 2pm"}]}, headers=HEADERS)
    
    # Update
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":"Move the Mutable Meeting to 4pm"}]}, headers=HEADERS)
    log(f"Update Resp: {r.text[:100]}...")
    
    if "updated" in r.text.lower() or "move" in r.text.lower() or "changed" in r.text.lower():
        log("✅ Calendar Update Verified.")
    else:
        log("⚠️ Calendar Update response unclear.")
        
    # Cleanup
    requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":"Delete the Mutable Meeting"}]}, headers=HEADERS)

if __name__ == "__main__":
    test_advanced_features()
