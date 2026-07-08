import os

import requests
from dotenv import load_dotenv

load_dotenv()
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
ENTITY_ID = "media_player.office_speaker"

def test_play(media_id, media_type):
    print(f"\n--- Testing: id='{media_id}' type='{media_type}' ---", flush=True)
    url = f"{HA_URL.rstrip('/')}/api/services/music_assistant/play_media"
    headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "entity_id": ENTITY_ID,
        "media_id": media_id,
        "media_type": media_type,
        "enqueue": "play"
    }

    try:
        r = requests.post(url, json=payload, headers=headers)
        print(f"Status: {r.status_code}")
        print(f"Response: {r.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    if not HA_URL:
        print("No HA_URL")
        exit(1)

    # Test 1: The configuration likely failing (Generic Music type)
    test_play("Brandon Lake", "music")

    # Test 2: The configuration known to work
    test_play("Brandon Lake", "artist")

    # Test 3: Search fallback?
    test_play("Brandon Lake", "search")
