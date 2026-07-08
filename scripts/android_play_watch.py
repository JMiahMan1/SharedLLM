#!/usr/bin/env python3
"""
Test Android TV Play and Watch with auto-power-on
"""
import time

import requests

BASE_URL = "http://ai.local:11435"
ANDROID_TV_ENTITY = "media_player.office_tv_chrome_2"

def chat(query):
    resp = requests.post(f"{BASE_URL}/api/chat", json={"query": query, "user": "admin"}, timeout=120)
    return resp.json().get('message', {}).get('content', 'N/A')

def get_state(entity_id):
    resp = requests.get(f"{BASE_URL}/api/ha/state/{entity_id}", timeout=5)
    data = resp.json()
    return data.get("state"), data.get("attributes", {}).get("app_id", "N/A")

print("=" * 70)
print("Android TV Play and Watch Test (with auto-power-on)")
print("=" * 70)

# Ensure TV is off
print("\n[SETUP] Ensuring TV is OFF...")
chat("Turn off Office TV")
time.sleep(8)
state, app = get_state(ANDROID_TV_ENTITY)
print(f"  Initial state: {state}")

# TEST 1: Play Music (should auto-power-on)
print("\n[TEST 1] Play Music while OFF (should auto-power-on)...")
print("  Command: 'Play Brandon Lake on Office TV'")
response = chat("Play Brandon Lake on Office TV")
print(f"  Response: {response}")

time.sleep(5)
state, app = get_state(ANDROID_TV_ENTITY)
print(f"  State after 5s: {state}, App: {app}")

if state == "playing":
    print("  ✅ PASS: TV auto-powered on and started playing!")
else:
    print(f"  ❌ FAIL: Expected 'playing', got '{state}'")

# Cleanup
print("\n[CLEANUP] Turning off...")
chat("Turn off Office TV")
time.sleep(8)

# TEST 2: Watch Video (should auto-power-on)
print("\n[TEST 2] Watch Video while OFF (should auto-power-on)...")
print("  Command: 'Watch Breaking Bad on Office TV'")
response = chat("Watch Breaking Bad on Office TV")
print(f"  Response: {response}")

time.sleep(5)
state, app = get_state(ANDROID_TV_ENTITY)
print(f"  State after 5s: {state}, App: {app}")

if state == "playing":
    print("  ✅ PASS: TV auto-powered on for watch!")
else:
    print(f"  ❌ FAIL: Expected 'playing', got '{state}'")

print("\n" + "=" * 70)
print("Test Complete")
print("=" * 70)
