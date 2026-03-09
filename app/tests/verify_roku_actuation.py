
import asyncio
import logging
import os
import sys
import requests
from dotenv import load_dotenv

# Add app to path
sys.path.append(os.getcwd())

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("verify_roku")
log.setLevel(logging.INFO)

load_dotenv()

HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
ENTITY_ID = "media_player.28_tcl_roku_tv"
REMOTE_ID = "remote.28_tcl_roku_tv" # Assuming standard naming, verified via previous run

headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

async def trigger_service(domain, service, entity_id, data={}):
    url = f"{HA_URL}/api/services/{domain}/{service}"
    payload = {"entity_id": entity_id, **data}
    log.info(f"Calling {domain}.{service} on {entity_id}...")
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        log.info(f"Response: {resp.status_code} - {resp.text}")
        return resp.status_code == 200
    except Exception as e:
        log.error(f"Error: {e}")
        return False

async def main():
    if not HA_URL or not HA_TOKEN:
        log.error("Missing HA_URL or HA_TOKEN in .env")
        return

    print(f"--- Live Actuation Test for {ENTITY_ID} ---")
    
    # 1. Check State
    try:
        s = requests.get(f"{HA_URL}/api/states/{ENTITY_ID}", headers=headers).json()
        print(f"Initial State: {s.get('state')} | App: {s.get('attributes', {}).get('app_name')}")
    except Exception as e:
        print(f"State fetch failed: {e}")

    # 2. Try Turn On
    print(">>> Sending Turn On...")
    await trigger_service("media_player", "turn_on", ENTITY_ID)
    await asyncio.sleep(2)
    
    # 3. Try Remote Home (Force Wake)
    print(">>> Sending Remote Home...")
    await trigger_service("remote", "send_command", REMOTE_ID, {"command": "Home"})
    await asyncio.sleep(2)
    
    # 4. Check State Again
    try:
        s = requests.get(f"{HA_URL}/api/states/{ENTITY_ID}", headers=headers).json()
        print(f"Final State: {s.get('state')} | App: {s.get('attributes', {}).get('app_name')}")
    except: pass

if __name__ == "__main__":
    asyncio.run(main())
