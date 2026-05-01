#!/usr/bin/env python3
"""
Test Android TV Watch with video download
"""
import requests
import time

BASE_URL = "http://192.168.2.205:11435"
ANDROID_TV_ENTITY = "media_player.office_tv_chrome_2"

def chat(query):
    resp = requests.post(f"{BASE_URL}/api/chat", json={"query": query, "user": "admin"}, timeout=180)
    return resp.json().get('message', {}).get('content', 'N/A')

def get_state(entity_id):
    resp = requests.get(f"{BASE_URL}/api/ha/state/{entity_id}", timeout=5)
    data = resp.json()
    return data.get("state"), data.get("attributes", {}).get("app_id", "N/A")

print("=" * 70)
print("Android TV Watch Test with Video Download")
print("=" * 70)

# Test with a simple search term
print("\n[TEST] Watch Breaking Bad (video download + local serve)...")
print("  This will download the video locally and serve as mp4")
response = chat("Watch Breaking Bad on Office TV")
print(f"  Response: {response}")

time.sleep(10)
state, app = get_state(ANDROID_TV_ENTITY)
print(f"  State after 10s: {state}, App: {app}")

if state == "playing":
    print("  ✅ SUCCESS: Video is playing!")
else:
    print(f"  Current state: {state}")
    print("  Check logs for video download progress")

print("\n" + "=" * 70)
