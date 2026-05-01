import sys
import os
import time
import requests
import json
from dotenv import load_dotenv

# Setup
script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
load_dotenv(os.path.join(root_dir, '.env'))

API_URL = os.getenv("API_URL", "http://192.168.2.205:11435")
HEADERS = {"Content-Type": "application/json", "X-RAG-User": "admin"}
SILENT_TOKEN = "[SILENT_SUCCESS]"

def log(msg):
    print(msg, flush=True)
    with open("full_system_test_results.txt", "a") as f:
        f.write(msg + "\n")

def send_chat(content):
    try:
        r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":content}], "stream":False}, headers=HEADERS, timeout=30)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

def test_media_extended():
    log("\n--- TEST: Media Extended (Volume/Transport) ---")
    
    # 1. Volume
    log("   [ACTION] Set volume to 50% on Office TV")
    resp = send_chat("Set volume to 50% on Office TV")
    msg = resp.get("message", {}).get("content", "")
    if SILENT_TOKEN in msg or "volume" in msg.lower():
        log("   [PASS] Volume command accepted.")
    else:
        log(f"   [FAIL] Volume command response unexpected: {msg}")

    # 2. Pause (Regex Check)
    log("   [ACTION] Pause the Office TV")
    resp = send_chat("Pause the Office TV")
    msg = resp.get("message", {}).get("content", "")
    if SILENT_TOKEN in msg or "paus" in msg.lower() or "stop" in msg.lower():
        log("   [PASS] Pause command accepted.")
    else:
        log(f"   [FAIL] Pause command response unexpected: {msg}")

def test_calendar_tools():
    log("\n--- TEST: Calendar Tools ---")
    
    # 1. Add Event
    ts = int(time.time())
    evt_title = f"Test Meeting {ts}"
    log(f"   [ACTION] Schedule '{evt_title}' tomorrow at 2pm")
    resp = send_chat(f"Schedule {evt_title} tomorrow at 2pm")
    msg = resp.get("message", {}).get("content", "")
    
    if "scheduled" in msg.lower() or "added" in msg.lower():
        log("   [PASS] Event creation reported success.")
    else:
        log(f"   [FAIL] Event creation response unexpected: {msg}")

    # 2. List Events
    log("   [ACTION] List my meetings")
    resp = send_chat("List my meetings")
    msg = resp.get("message", {}).get("content", "")
    if evt_title in msg:
        log(f"   [PASS] Found '{evt_title}' in calendar list.")
    else:
        log(f"   [WARN] Did not find '{evt_title}' in list. Response: {msg[:100]}...")

def test_note_tools():
    log("\n--- TEST: Note Tools ---")
    
    # 1. Create Note
    log("   [ACTION] Create a note called 'System Check' saying 'All systems go'")
    resp = send_chat("Create a note called 'System Check' saying 'All systems go'")
    msg = resp.get("message", {}).get("content", "")
    
    if "created" in msg.lower() or "saved" in msg.lower():
        log("   [PASS] Note creation reported success.")
    else:
        log(f"   [FAIL] Note creation response unexpected: {msg}")
        
    # 2. Read Note
    log("   [ACTION] Read my 'System Check' note")
    resp = send_chat("Read my 'System Check' note")
    msg = resp.get("message", {}).get("content", "")
    if "All systems go" in msg:
        log("   [PASS] Note content verified.")
    else:
        log(f"   [FAIL] Note content not found. Response: {msg}")

def test_web_search():
    log("\n--- TEST: Web Search ---")
    log("   [ACTION] Search for 'current time in Tokyo'")
    resp = send_chat("Search for the current time in Tokyo")
    msg = resp.get("message", {}).get("content", "")
    
    # Check for actual search results logic (assuming tool output is in response)
    if "Tokyo" in msg and any(char.isdigit() for char in msg):
         log("   [PASS] Search returned plausible result.")
    else:
         log(f"   [WARN] Search result unclear: {msg[:100]}...")

def test_timer_extended():
    log("\n--- TEST: Timer Extended ---")
    log("   [ACTION] Set a timer for 10 minutes")
    resp = send_chat("Set a timer for 10 minutes")
    msg = resp.get("message", {}).get("content", "")
    if "set" in msg.lower() and "10 minute" in msg.lower():
        log("   [PASS] Timer set.")
    else:
        log(f"   [FAIL] Timer set response: {msg}")
        
    log("   [ACTION] Pause the timer")
    resp = send_chat("Pause the timer")
    msg = resp.get("message", {}).get("content", "")
    if "paused" in msg.lower():
        log("   [PASS] Timer paused.")
    else:
        log(f"   [FAIL] Timer pause response: {msg}")

if __name__ == "__main__":
    with open("full_system_test_results.txt", "w") as f: 
        f.write(f"Full System Verification Run: {time.ctime()}\n")
    
    log(f"Targeting API: {API_URL}")
    test_media_extended()
    test_calendar_tools()
    test_note_tools()
    test_web_search()
    test_timer_extended()
