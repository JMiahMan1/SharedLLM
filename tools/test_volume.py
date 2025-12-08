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
    print("[FAIL] Error: HA_URL or HA_TOKEN not set in .env")
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
    print("\n1. Playing Music on Office Speaker (MA)...")
    # Using MA directly to ensure music context
    # Try generic play logic via our pipeline? No, let's direct hit to ensure state.
    # Actually, user wants to test OUR routing.
    # So we should hit OUR API.

    API_URL = os.getenv("RAG_API_URL", "http://192.168.2.211:11435/api/chat")
    print(f"Using API URL: {API_URL}")

    async def chat(q):
        print(f"\nUser: {q}")
        try:
            print(f"   [INFO] Sending request (timeout: 120s)...")
            r = requests.post(
                API_URL, json={"query": q, "user_id": "test_user"}, timeout=120
            )
            if r.status_code == 200:
                response = r.json()
                print(f"Bot: {response.get('response', '')}")
                return response
            else:
                print(f"[FAIL] API returned status {r.status_code}: {r.text[:200]}")
                return {}
        except requests.exceptions.Timeout:
            print(f"[FAIL] API Timeout: Request took longer than 120s")
            print(f"   This suggests Ollama connectivity or model loading issues")
            return {}
        except Exception as e:
            print(f"[FAIL] API Error: {type(e).__name__}: {e}")
            return {}

    async def test_step(prompt, entity_id, expected_vol):
        print(f"\nUser: {prompt}")
        await chat(prompt)
        await asyncio.sleep(3)
        
        curr_vol = await get_volume(entity_id)
        if curr_vol is None:
             print(f"[WARN] Could not read volume for {entity_id}")
             return

        # Check with tolerance
        if abs(curr_vol - expected_vol) < 0.05:
            print(f"[OK] Success: Volume is {curr_vol} (Target: {expected_vol})")
        else:
            print(f"[FAIL] Expected {expected_vol}, got {curr_vol}")

    targets = ["media_player.office_tv", "media_player.office_speaker"]
    
    for entity_id in targets:
        print(f"\n--- Starting Volume Lifecycle Test on {entity_id} ---")
        
        # Derive a friendly name for the prompt
        name = entity_id.split(".")[-1].replace("_", " ").title()

        # 0. Wake up / Play Music
        print(f"0. Activating {name}...")
        # Use a Radio URL to guarantee playback (no search required)
        song = "http://icecast.omroep.nl/3fm-bb-mp3"
        await chat(f"Play {song} on {name}")
        await asyncio.sleep(5)
        
        # Get Initial Volume
        init_vol = await get_volume(entity_id)
        print(f"Initial Volume: {init_vol}")
        if init_vol is None:
             print(f"[WARN] Device {name} appears off or not reporting volume.")
             init_vol = 0.5
        
    # 1. Set Volume Low
        await test_step(
            f"Set the volume on {name} to 30%", 
            entity_id, 
            0.3
        )
        
        # 2. Set Volume High
        await test_step(
            f"Turn the volume up to 60% on {name}", 
            entity_id, 
            0.6
        )

        # 3. Restore
        print(f"\n4. Restoring to {init_vol * 100}%...")
        await chat(f"Set volume on {name} to {int(init_vol * 100)}%")
        await asyncio.sleep(2)
        final_vol = await get_volume(entity_id)
        print(f"Final Volume: {final_vol}")


if __name__ == "__main__":
    asyncio.run(run_test())
