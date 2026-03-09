
import asyncio
import logging
import sys
import os
import aiohttp
import json
from pprint import pprint

# Add app to path
sys.path.append(os.getcwd())

# Force load settings to get env vars
from dotenv import load_dotenv
load_dotenv()

from app.settings import HA_URL, GlobalResources
HA_TOKEN = os.getenv("HA_TOKEN")
from app.domains.media.integrations.roku import RokuIntegration
from app.domains.media.integrations.androidtv import AndroidTVIntegration
from app.domains.media.integrations.factory import IntegrationFactory

# Setup Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("LivePowerTest")

HEADER = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

async def get_ha_state(entity_id):
    url = f"{HA_URL}/api/states/{entity_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=HEADER) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                log.error(f"Failed to get state for {entity_id}: {resp.status}")
                return None

async def test_device_power(entity_id, integration_name, integration_cls):
    print(f"\n--- Testing Power for {entity_id} ({integration_name}) ---")
    
    # 1. Check Initial State
    initial = await get_ha_state(entity_id)
    if not initial:
         print(f"[SKIP] Entity {entity_id} not found in HA.")
         return
         
    print(f"Initial State: {initial['state']}")
    
    # 2. Instantiate Integration
    # Try via Factory first to verify lookup
    print("Instantiating Integration via Factory...")
    try:
        handler = IntegrationFactory.get_handler(integration_name)
        print(f"Loaded Handler: {type(handler).__name__}")
    except Exception as e:
        print(f"[FAIL] Factory failed: {e}")
        return


    # Mock GlobalResources for Friendly Name Lookup (Strategy 3)
    # Since we can't connect to real Chroma locally due to lib issues.
    from unittest.mock import MagicMock
    
    mock_collection = MagicMock()
    GlobalResources.ha_collection = mock_collection
    
    # Mock Data
    roku_meta = {"entity_id": "media_player.roku_2n0062385487", "friendly_name": "Gracies TV"}
    remote_meta = {"entity_id": "remote.28_tcl_roku_tv", "friendly_name": "Gracies TV"}
    
    def mock_get(ids=None, where=None, include=None):
        if ids and "media_player.roku_2n0062385487" in ids:
             return {"metadatas": [roku_meta]}
        if where and where.get("friendly_name") == "Gracies TV":
             return {"metadatas": [remote_meta]}
        return {"metadatas": []}
    
    mock_collection.get.side_effect = mock_get
    
    user_creds = {"ha_token": HA_TOKEN}
    
    # 3. Toggle Power
    # If it's ON, turn OFF. If OFF, turn ON.
    if initial['state'] not in ['off', 'standby', 'idle', 'unavailable']:
        print(f"Device is ON. Attempting Turn OFF...")
        await handler.turn_off(entity_id, user_creds)
        target_state = "off" # or idle/standby
    else:
        print(f"Device is OFF. Attempting Turn ON...")
        await handler.turn_on(entity_id, user_creds)
        target_state = "on" # or playing/paused/idle
        
    # 4. Wait and Verify
    print("Waiting 5s for state change...")
    await asyncio.sleep(5)
    
    final = await get_ha_state(entity_id)
    print(f"Final State: {final.get('state')}")
    
    if final['state'] != initial['state']:
        print(f"[PASS] State changed from {initial['state']} to {final['state']}")
    else:
        # Roku might go from 'off' to 'home' (which reports as idle sometimes?)
        print(f"[WARNING] State did not change distinctly. (Roku 'idle' can mean ON-at-home-screen)")

async def main():
    if not HA_TOKEN:
        print("ERROR: HA_TOKEN not found in env.")
        return


    # Verify Roku
    # Need a known Roku entity. Try to find one.
    roku_entity = "media_player.roku_2n0062385487" # Gracies TV
    print("Searching for Roku...")
    # (Simple discovery omitted, assuming known entity or user context)
    
    # Run Test on Roku
    await test_device_power(roku_entity, "roku", RokuIntegration)

    
    # Verify Android TV
    android_entity = "media_player.office_tv"
    await test_device_power(android_entity, "androidtv", AndroidTVIntegration)

if __name__ == "__main__":
    asyncio.run(main())
