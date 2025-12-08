import asyncio
import os
import sys
import json
import logging
import requests
from dotenv import load_dotenv


# settings import might fail if dependencies aren't perfect, let's mock credits
load_dotenv()

HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
OFFICE_TV = "media_player.office_tv"  # Hardware
OFFICE_TV_CHROME = "media_player.office_tv_chrome"  # Cast
OFFICE_REMOTE = "remote.office_tv_remote"
OFFICE_SPEAKER = "media_player.office_speaker"  # Music Assistant?

if not HA_URL or not HA_TOKEN:
    print("❌ Error: HA_URL or HA_TOKEN not set in .env")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("test_volume")

creds = {"ha_token": HA_TOKEN, "user": "test_user"}


async def get_volume(entity_id):
    url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {HA_TOKEN}"}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        data = r.json()
        vol = data.get("attributes", {}).get("volume_level")
        return vol
    return None


async def run_test():
    print(f"--- Starting Volume Lifecycle Test on {OFFICE_TV} ---")

    # 1. Start Music (to ensure it's active)
    print("\n1. ▶️ Playing Music on Office Speaker (MA)...")
    # Using MA directly to ensure music context
    # Try generic play logic via our pipeline? No, let's direct hit to ensure state.
    # Actually, user wants to test OUR routing.
    # So we should hit OUR API.

    API_URL = os.getenv("RAG_API_URL", "http://192.168.2.211:11435/api/chat")
    print(f"📡 Using API URL: {API_URL}")

    def chat(q):
        print(f"\nUser: {q}")
        try:
            r = requests.post(
                API_URL, json={"query": q, "user_id": "test_user"}, timeout=30
            )
            print(f"Bot: {r.json().get('response', '')}")
            return r.json()
        except Exception as e:
            print(f"❌ API Error: {e}")
            return {}

    # Play Music
    chat("Play 'The Dark Side of the Moon' on Office Speaker")
    await asyncio.sleep(5)  # Wait for buffer

    # Get Initial Volume
    init_vol = await get_volume(OFFICE_SPEAKER)
    print(f"📊 Initial Volume: {init_vol}")

    if init_vol is None:
        print(
            "⚠️ Warning: Could not read volume. Device might not support it or be off."
        )
        init_vol = 0.5

    # 2. Set Volume Low
    print("\n2. 🔉 Setting Volume to 30%...")
    chat("Set the volume on Office Speaker to 30%")
    await asyncio.sleep(3)

    curr_vol = await get_volume(OFFICE_SPEAKER)
    print(f"📊 Volume is now: {curr_vol}")

    if curr_vol == 0.3:
        print("✅ Success: Volume matches 30%")
    else:
        print(f"❌ Failure: Expected 0.3, got {curr_vol}")

    # 3. Set Volume High
    print("\n3. 🔊 Setting Volume to 60%...")
    chat("Turn the volume up to 60%")
    await asyncio.sleep(3)

    curr_vol = await get_volume(OFFICE_SPEAKER)
    print(f"📊 Volume is now: {curr_vol}")

    if curr_vol == 0.6:
        print("✅ Success: Volume matches 60%")
    else:
        print(f"❌ Failure: Expected 0.6, got {curr_vol}")

    # 4. Restore
    print(f"\n4. 🔄 Restoring to {init_vol * 100}%...")
    chat(f"Set volume to {int(init_vol * 100)}%")
    await asyncio.sleep(2)
    final_vol = await get_volume(OFFICE_SPEAKER)
    print(f"📊 Final Volume: {final_vol}")


if __name__ == "__main__":
    asyncio.run(run_test())
