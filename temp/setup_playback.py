
import requests
import os
import json

HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
HA_TOKEN = os.getenv("HA_TOKEN", "")
DEVICE_ENTITY = "media_player.office_tv_chrome_2"
VIDEO_URL = "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"

headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

data = {
    "entity_id": DEVICE_ENTITY,
    "media_content_id": VIDEO_URL,
    "media_content_type": "video/mp4"
}

print(f"Force-starting playback on {DEVICE_ENTITY}...")
try:
    resp = requests.post(f"{HA_URL}/api/services/media_player/play_media", headers=headers, json=data, timeout=10)
    print(f"Response: {resp.status_code}")
    print(resp.text)
except Exception as e:
    print(f"Error: {e}")
