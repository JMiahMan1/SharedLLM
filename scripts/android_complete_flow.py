#!/usr/bin/env python3
"""
Test Android TV Watch flow with Phil Wickham video
Tests: Watch -> Pause -> Resume -> Stop -> Turn Off
"""
import time

import requests

BASE_URL = "http://ai.local:11435"
# Use specific entity ID to check state
ANDROID_TV_ENTITY = "media_player.office_tv_chrome_2"

def chat(query):
    print(f"  [Chat] Sending: '{query}'")
    resp = requests.post(f"{BASE_URL}/api/chat", json={"query": query, "user": "admin"}, timeout=180)
    return resp.json().get('message', {}).get('content', 'N/A')

def get_state(entity_id):
    resp = requests.get(f"{BASE_URL}/api/ha/state/{entity_id}", timeout=5)
    data = resp.json()
    return data.get("state"), data.get("attributes", {}).get("app_id", "N/A")

print("=" * 70)
print("Android TV Complete Flow Test - Phil Wickham")
print("=" * 70)

# Ensure TV is off
print("\n[SETUP] Turning off TV...")
chat("Turn off Office TV")
time.sleep(8)
state, app = get_state(ANDROID_TV_ENTITY)
print(f"  State: {state}")

# Test 1: Watch video (should auto-power on and download/play)
print("\n[1] Watch Phil Wickham video...")
response = chat("Watch Phil Wickham on Office TV")
print(f"  Response: {response[:100]}...")

print("  Waiting for video to download and start playing (15s)...")
time.sleep(15)
state, app = get_state(ANDROID_TV_ENTITY)
print(f"  State: {state}, App: {app}")

if state == "playing":
     print("  ✅ Video is playing!")
else:
     print(f"  ⚠️  State is '{state}'")


# Test 2: Pause
print("\n[2] Pause video...")
response = chat("Pause")
print(f"  Response: {response[:80]}")
time.sleep(3)
state, app = get_state(ANDROID_TV_ENTITY)
print(f"  State: {state}, App: {app}")

if state == "paused":
    print("  ✅ Paused successfully!")
else:
    print(f"  Current state: {state}")

# Test 3: Resume
print("\n[3] Resume video...")
response = chat("Resume")
print(f"  Response: {response[:80]}")
time.sleep(3)
state, app = get_state(ANDROID_TV_ENTITY)
print(f"  State: {state}, App: {app}")

if state == "playing":
    print("  ✅ Resumed successfully!")
else:
    print(f"  Current state: {state}")

# Test 4: Stop
print("\n[4] Stop video...")
response = chat("Stop")
print(f"  Response: {response[:80]}")
time.sleep(5)
state, app = get_state(ANDROID_TV_ENTITY)
print(f"  State: {state}, App: {app}")

if state in ["idle", "off"]:
    print("  ✅ Stopped successfully!")
else:
    print(f"  Current state: {state}")

# Test 5: Turn Off
print("\n[5] Turn Off TV...")
response = chat("Turn Off")
print(f"  Response: {response[:80]}")
time.sleep(8)
state, app = get_state(ANDROID_TV_ENTITY)
print(f"  State: {state}")

if state == "off":
    print("  ✅ Turned Off successfully!")
else:
    print(f"  ⚠️  Current state: {state}")

print("\n" + "=" * 70)
print("Test Complete")
print("=" * 70)
