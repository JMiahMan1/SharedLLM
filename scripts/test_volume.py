import asyncio
import logging
import os
import sys

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

    api_url_raw = os.getenv("RAG_ADDRESS")
    if not api_url_raw:
        print("[FAIL] Error: RAG_ADDRESS not set in .env")
        return
    # Add http:// if not present
    if not api_url_raw.startswith("http"):
        rag_api_url = f"http://{api_url_raw}:11435"
    else:
        rag_api_url = api_url_raw
    if not rag_api_url.endswith("/api/chat"):
        rag_api_url = f"{rag_api_url}/api/chat"
    print(f"Using API URL: {rag_api_url}")

    async def chat(q):
        print(f"\nUser: {q}")
        try:
            print("   [INFO] Sending request (timeout: 120s)...")
            r = requests.post(
                rag_api_url, json={"query": q, "user_id": "test_user"}, timeout=120
            )
            if r.status_code == 200:
                response = r.json()
                print(f"Bot: {response.get('response', '')}")
                return response
            else:
                print(f"[FAIL] API returned status {r.status_code}: {r.text[:200]}")
                return {}
        except requests.exceptions.Timeout:
            print("[FAIL] API Timeout: Request took longer than 120s")
            print("   This suggests Ollama connectivity or model loading issues")
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

    # Test both devices as requested
    targets = [
        ("media_player.office_tv_chrome_2", "Office TV"),
        ("media_player.office_speaker", "Office Speaker")
    ]

    for entity_id, name in targets:
        print(f"\n--- Starting Volume Lifecycle Test on {entity_id} ({name}) ---")

        # 0. Wake up / Play Music
        print(f"0. Activating {name}...")
        # User requested local library test (Artist)
        song = "Brandon Lake"
        await chat(f"Play {song} on {name}")

        # VERIFY STATE CHANGE (Strict)
        print("   Verifying playback state...", end="", flush=True)
        is_playing = False
        for _ in range(10):
            await asyncio.sleep(1)
            url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
            try:
                state_resp = requests.get(url, headers={"Authorization": f"Bearer {HA_TOKEN}"})
                if state_resp.json().get("state") == "playing":
                    print(" [OK] State: playing")
                    is_playing = True
                    break
                print(".", end="", flush=True)
            except: pass

        if not is_playing:
            print(f" [FAIL] Device failed to start playing {song}. Proceeding to Volume Test anyway (to verify control).")
            # continue  <-- Commented out to force volume verification

        await asyncio.sleep(2)

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
