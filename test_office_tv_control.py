
import asyncio
import sys
import json
import time
import requests
import os

# Add app directory to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

# Fix Logging Path for Test
os.environ["LOG_FILE"] = "./test_app.log"

from settings import HA_URL, get_user_creds, DEFAULT_MODEL, load_resources
from logic import pipeline
from logic.refresh_devices import refresh_db

USER = "jeremiah"
ENTITY_ID = "media_player.office_tv"
DEVICE_NAME = "Office TV"

def get_state():
    # ... existing get_state implementation ...
    creds = get_user_creds(USER)
    token = creds.get("ha_token")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = requests.get(f"{HA_URL.rstrip('/')}/api/states/{ENTITY_ID}", headers=headers, timeout=5)
        if r.status_code == 200:
            return r.json()
    except: pass
    return None

async def send_chat_command(command):
    # ... existing send_chat_command implementation ...
    print(f"\n[USER]: {command}")
    gen = pipeline.generate_rag_stream(command, USER, DEFAULT_MODEL, False, "chat")
    response = ""
    async for chunk in gen:
        try:
            if chunk.strip() and not chunk.startswith("data: [DONE]"):
                data = json.loads(chunk.replace("data: ", ""))
                if "message" in data:
                     response += data["message"].get("content", "")
        except: pass
    print(f"[ASSISTANT]: {response.strip()}")
    return response

async def run_test():
    print("--- Initializing Resources (Chroma/Redis) ---")
    await load_resources()
    print("--- Refreshing Device DB ---")
    await refresh_db()
    
    print(f"--- Testing Control for {DEVICE_NAME} ({ENTITY_ID}) ---")
    
    # 1. Check Initial State
    initial = get_state()
    print(f"Initial State: {initial.get('state') if initial else 'Unknown'}")
    
    # 2. Turn On
    await send_chat_command(f"Turn on {DEVICE_NAME}")
    print("Waiting 15s for power on...") # Increased wait
    time.sleep(15)
    
    state_after_on = get_state()
    curr_state = state_after_on.get('state') if state_after_on else 'Unknown'
    print(f"State after ON: {curr_state}")
    
    # 3. Play Audio (Launch App) - As requested before Volume
    await send_chat_command(f"Launch YouTube on {DEVICE_NAME}")
    time.sleep(8)
    state_app = get_state()
    src = state_app.get('attributes', {}).get('source') if state_app else None
    print(f"Current App/Source: {src}")

    # 4. Volume Verification
    await send_chat_command(f"Set volume to 25% on {DEVICE_NAME}")
    time.sleep(3)
    
    state_vol = get_state()
    vol = state_vol.get('attributes', {}).get('volume_level') if state_vol else None
    print(f"Volume Level: {vol}")
    
    # 5. Mute Verification
    await send_chat_command(f"Mute {DEVICE_NAME}")
    time.sleep(2)
    state_mute = get_state()
    is_muted = state_mute.get('attributes', {}).get('is_volume_muted') if state_mute else None
    print(f"Muted: {is_muted}")

    # 6. Turn Off
    await send_chat_command(f"Turn off {DEVICE_NAME}")
    print("Waiting 5s for power off...")
    time.sleep(5)
    
    final = get_state()
    print(f"Final State: {final.get('state') if final else 'Unknown'}")

if __name__ == "__main__":
    try:
        asyncio.run(run_test())
    except KeyboardInterrupt:
        pass
