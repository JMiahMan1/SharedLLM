#!/usr/bin/env python3
"""
Test Android TV with valid video URL for Watch intent
"""
import requests
import time

BASE_URL = "http://192.168.2.211:11435"
ANDROID_TV_ENTITY = "media_player.office_tv_chrome_2"

# Use a real video URL
VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

def chat(query):
    resp = requests.post(f"{BASE_URL}/api/chat", json={"query": query, "user": "admin"}, timeout=120)
    return resp.json().get('message', {}).get('content', 'N/A')

def get_state(entity_id):
    resp = requests.get(f"{BASE_URL}/api/ha/state/{entity_id}", timeout=5)
    data = resp.json()
    return data.get("state"), data.get("attributes", {}).get("app_id", "N/A")

print("=" * 70)
print("Android TV Watch Test with Real Video URL")
print("=" * 70)

# Ensure TV is off
print("\n[SETUP] Turning off TV...")
chat("Turn off Office TV")
time.sleep(10)
state, app = get_state(ANDROID_TV_ENTITY)
print(f"  Initial state: {state}")

if state != "off":
    print(f"  WARNING: TV didn't turn off, state is '{state}'")
    print("  Proceeding anyway...")

# Test Watch with YouTube URL
print(f"\n[TEST] Watch YouTube video while TV is off...")
print(f"  Command: 'Watch {VIDEO_URL} on Office TV'")
response = chat(f"Watch {VIDEO_URL} on Office TV")
print(f"  Response: {response}")

time.sleep(8)
state, app = get_state(ANDROID_TV_ENTITY)
print(f"  State after 8s: {state}, App: {app}")

if state == "playing":
    print("  ✅ SUCCESS: TV turned on and video is playing!")
else:
    print(f"  ❌ FAILED: Expected 'playing', got '{state}'")

print("\n" + "=" * 70)
print("=" * 70)
