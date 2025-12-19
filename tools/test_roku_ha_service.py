import requests
import os
import sys
import json

# Configuration
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
ENTITY_ID = "media_player.28_tcl_roku_tv"
VIDEO_ID = "aqz-KE-bpKQ" # Big Buck Bunny

if not HA_URL or not HA_TOKEN:
    print("Missing HA_URL or HA_TOKEN")
    sys.exit(1)

headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

def test_payload(name, payload):
    print(f"\n--- Testing: {name} ---")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    try:
        url = f"{HA_URL}/api/services/media_player/play_media"
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        
        print(f"Status: {resp.status_code}")
        if resp.status_code != 200:
             print(f"Error: {resp.text}")
        else:
             print("Success! (Check TV)")
             
    except Exception as e:
        print(f"Exception: {e}")

# 0. Ensure TV is ON
print("\n--- Step 0: Turning TV ON ---")
try:
    url = f"{HA_URL}/api/services/media_player/turn_on"
    resp = requests.post(url, headers=headers, json={"entity_id": ENTITY_ID}, timeout=10)
    print(f"Turn On Status: {resp.status_code}")
except Exception as e:
    print(f"Turn On Failed: {e}")

import time
print("Waiting 10s for wake up...")
time.sleep(10)

# Payload 1: Deep Link String (Current Implementation)
p1 = {
    "entity_id": ENTITY_ID,
    "media_content_id": f"837?contentId={VIDEO_ID}&mediaType=live",
    "media_content_type": "app"
}
test_payload("String Deep Link", p1)

# Payload 2: App ID with Extra (Alternate)
p2 = {
    "entity_id": ENTITY_ID,
    "media_content_id": "837",
    "media_content_type": "app",
    "extra": {
        "contentId": VIDEO_ID,
        "mediaType": "live"
    }
}
test_payload("Extra Dict", p2)
