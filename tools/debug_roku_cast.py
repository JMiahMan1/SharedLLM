import requests
import os
import json
import time

HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
ENTITY_ID = "media_player.28_tcl_roku_tv"
# Public safe MP4 for testing
TEST_VIDEO_URL = "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"

headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

def call_ha(service, payload):
    url = f"{HA_URL}/api/services/{service}"
    print(f"\nSending [{service}] Payload:\n{json.dumps(payload, indent=2)}")
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        print(f"Response: {resp.status_code}")
        if resp.status_code >= 400:
            print(f"Error: {resp.text}")
        return resp.status_code == 200
    except Exception as e:
        print(f"Exception: {e}")
        return False

# 1. Ensure TV is ON
print("--- Check Safe Wake ---")
# call_ha("media_player/turn_on", {"entity_id": ENTITY_ID})
# time.sleep(5) # Give it a moment if it was off

# 2. Test Playloads
# A. Standard 'video' type (Most likely correct)
payload_a = {
    "entity_id": ENTITY_ID,
    "media_content_id": TEST_VIDEO_URL,
    "media_content_type": "video",
    "extra": {
        "title": "Debug Cast A",
        "format": "mp4"
    }
}

# B. 'url' type (What I used before - suspect cause of 500)
payload_b = {
    "entity_id": ENTITY_ID,
    "media_content_id": TEST_VIDEO_URL,
    "media_content_type": "url",
    "extra": {
        "title": "Debug Cast B",
        "format": "mp4"
    }
}

# C. No Extra
payload_c = {
    "entity_id": ENTITY_ID,
    "media_content_id": TEST_VIDEO_URL,
    "media_content_type": "video"
}

print("Testing Payload A (type=video)...")
call_ha("media_player/play_media", payload_a)

print("\nTesting Payload B (type=url - EXPECT FAILURE)...")
call_ha("media_player/play_media", payload_b)

print("\nTesting Payload C (Minimal)...")
call_ha("media_player/play_media", payload_c)
