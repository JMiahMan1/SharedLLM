#!/usr/bin/env python3
"""
Test auto-power-on for Android TV when sending play command while device is off
"""
import requests
import time

BASE_URL = "http://192.168.2.211:11435"
ANDROID_TV_ENTITY = "media_player.office_tv_chrome_2"

def chat(query):
    resp = requests.post(f"{BASE_URL}/api/chat", json={"query": query, "user": "admin"}, timeout=120)
    return resp.json().get('message', {}).get('content', 'N/A')

def get_state(entity_id):
    resp = requests.get(f"{BASE_URL}/api/ha/state/{entity_id}", timeout=5)
    data = resp.json()
    return data.get("state"), data.get("attributes", {}).get("app_id", "N/A")

print("=" * 70)
print("Android TV Auto Power-On Test")
print("=" * 70)

# Ensure TV is off first
print("\n[1] Ensuring TV is OFF...")
state, app = get_state(ANDROID_TV_ENTITY)
print(f"  Current state: {state}")

if state != "off":
    print("  Turning off first...")
    chat("Turn off Office TV")
    time.sleep(10)
    state, app = get_state(ANDROID_TV_ENTITY)
    print(f"  State after turn off: {state}")

# Now try to play music while TV is off
print("\n[2] Sending 'Play Brandon Lake on Office TV' while device is OFF...")
response = chat("Play Brandon Lake on Office TV")
print(f"  Response: {response}")

# Wait and check if TV turned on
print("\n[3] Waiting 5 seconds for auto-power-on...")
time.sleep(5)
state, app = get_state(ANDROID_TV_ENTITY)
print(f"  State: {state}, App: {app}")

if state == "playing":
    print("\n✅ SUCCESS: TV auto-powered on and started playing!")
else:
    print(f"\n❌ FAILED: TV state is '{state}' instead of 'playing'")

print("\n" + "=" * 70)
print("Check logs for '[StandardIntegration] Auto-powering on' message")
print("=" * 70)
