
import asyncio
import logging
import sys
import os
from unittest.mock import MagicMock, patch

# Add app to path
sys.path.append(os.getcwd())

from dotenv import load_dotenv
load_dotenv()
from app.settings import GlobalResources, HA_URL
HA_TOKEN = os.getenv("HA_TOKEN")

# 2. Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("RokuIntentTest")

# 3. Imports
from app.domains.media.integrations.media_assistant_roku import RokuMediaAssistantIntegration


# LIVE TEST - NO MOCKS
# We implement a partial Chroma Collection interface that queries HA API directly
# This allows the integration code to run locally but fetch REAL data from the server.

class LiveHACollection:
    def __init__(self, ha_url, ha_token):
        self.ha_url = ha_url
        self.headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}

    def get(self, ids=None, where=None, include=None):
        # We only implement what _get_roku_remote needs:
        # 1. get(ids=[entity_id]) -> returns {metadatas: [{group_id, friendly_name}]}
        # 2. get(where={friendly_name}) -> returns {metadatas: [{entity_id}]}
        
        import requests
        results = []
        
        # Helper to fetch all states (expensive but simple for test)
        # In a real app we'd use the cache, but this is a live test script.
        try:
            resp = requests.get(f"{self.ha_url}/api/states", headers=self.headers)
            if resp.status_code == 200:
                all_states = resp.json()
            else:
                print(f"HA API Error: {resp.status_code}")
                return {"metadatas": []}
        except Exception as e:
            print(f"Connection Error: {e}")
            return {"metadatas": []}

        # Logic for IDs lookup
        if ids:
            for s in all_states:
                if s['entity_id'] in ids:
                    # Shim metadata format
                    meta = {
                        "entity_id": s['entity_id'],
                        "friendly_name": s['attributes'].get('friendly_name'),
                        "group_id": "live_group_shim" # We don't have real groups in API without registry, but we rely on Friendly Name strategy anyway
                    }
                    results.append(meta)
        
        # Logic for Friendly Name lookup
        if where and "friendly_name" in where:
            target_name = where["friendly_name"]
            for s in all_states:
                fname = s['attributes'].get('friendly_name')
                if fname == target_name:
                     meta = {
                        "entity_id": s['entity_id'],
                        "friendly_name": fname,
                         "group_id": "live_group_shim"
                     }
                     results.append(meta)

        return {"metadatas": results}

async def run_test():
    print("\n=== Roku Intent Verification (LIVE - No Mock) ===")
    
    # Setup Live Data Source
    GlobalResources.ha_collection = LiveHACollection(HA_URL, HA_TOKEN)
    
    # Instantiate Integration
    integration = RokuMediaAssistantIntegration()
    entity_id = "media_player.roku_2n0062385487" # Gracies TV
    user_creds = {
        "ha_token": HA_TOKEN,
        "ha_url": HA_URL
    }
    # Note: We rely on the internal 'requests' calls in the integration to actually hit HA.
    # We DO NOT mock execute_ha_service or requests.
    # However, 'execute_ha_service' might need to be imported/available. 
    # It is imported in the module. live_power_test showed it works.
    
    # 1. PLAY MUSIC INTENT
    print(f"\n[Action] Calling play_media(music) - Tim Timmons on {entity_id}")
    res = await integration.play_media(entity_id, "play Tim Timmons", "music", user_creds)
    print(f"Result: {res}")

    # 2. WAIT
    print("Waiting 10s...")
    await asyncio.sleep(10)

    # 3. WATCH VIDEO INTENT
    print(f"\n[Action] Calling play_media(video) - Tim Timmons on {entity_id}")
    res = await integration.play_media(entity_id, "watch Tim Timmons", "video", user_creds)
    print(f"Result: {res}")


if __name__ == "__main__":
    asyncio.run(run_test())
