#!/usr/bin/env python3
"""
Test the actual CastIntegration flow via Home Assistant API
"""
import os
import requests
import time

HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
HA_TOKEN = os.getenv("HA_TOKEN")

if not HA_TOKEN:
    print("ERROR: HA_TOKEN not set")
    exit(1)

headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

# Test video
VIDEO_URL = "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"
ENTITY_ID = "media_player.office_tv_chrome"

print(f"--- Testing Cast via Home Assistant API ---")
print(f"Target: {ENTITY_ID}")
print(f"Video: {VIDEO_URL}")

# 1. Check entity state first
print("\n1. Checking entity state...")
response = requests.get(
    f"{HA_URL}/api/states/{ENTITY_ID}",
    headers=headers,
    timeout=10
)
print(f"Status: {response.status_code}")
if response.status_code == 200:
    state = response.json()
    print(f"Entity state: {state['state']}")
    print(f"Friendly name: {state['attributes'].get('friendly_name')}")
else:
    print(f"ERROR: {response.text}")
    exit(1)

# 2. Send play_media command
print("\n2. Sending play_media command...")
payload = {
    "entity_id": ENTITY_ID,
    "media_content_id": VIDEO_URL,
    "media_content_type": "video/mp4"
}

response = requests.post(
    f"{HA_URL}/api/services/media_player/play_media",
    headers=headers,
    json=payload,
    timeout=10
)

print(f"Status: {response.status_code}")
if response.status_code == 200:
    print("SUCCESS: Command sent to Home Assistant")
    print(response.json())
else:
    print(f"ERROR: {response.text}")
    exit(1)

# 3. Wait and check state
print("\n3. Waiting 5 seconds then checking state...")
time.sleep(5)

response = requests.get(
    f"{HA_URL}/api/states/{ENTITY_ID}",
    headers=headers,
    timeout=10
)

if response.status_code == 200:
    state = response.json()
    print(f"New state: {state['state']}")
    print(f"Media title: {state['attributes'].get('media_title', 'N/A')}")
    print(f"App name: {state['attributes'].get('app_name', 'N/A')}")
    
    if state['state'] in ['playing', 'buffering']:
        print("\n✅ SUCCESS: Video is playing via Home Assistant!")
    else:
        print(f"\n⚠️  State is '{state['state']}' - may need more time to buffer")
else:
    print(f"ERROR checking final state: {response.text}")

print("\nTest complete.")
