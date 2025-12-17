
import asyncio
import os
import sys
import json
from app.settings import get_user_creds
from app.logic.media_ops import get_entity_state, execute_ha_service

async def main():
    user_creds = get_user_creds("system")
    # For local debug, we need to ensure we can reach HA.
    # The get_entity_state uses requests.get to HA_URL.
    
    entity_id = "media_player.28_tcl_roku_tv"
    print(f"Checking {entity_id}...")
    
    # from app.logic.discovery.integration_helper import get_ha_url_token
    import requests
    # ha_url, ha_token = get_ha_url_token(user_creds)
    ha_url = user_creds.get("ha_url")
    ha_token = user_creds.get("ha_token")
    headers = {"Authorization": f"Bearer {ha_token}", "Content-Type": "application/json"}
    
    try:
        r = requests.get(f"{ha_url}/api/states/{entity_id}", headers=headers)
        if r.status_code == 200:
            data = r.json()
            print(f"State: {data['state']}")
            print(f"Attributes: {json.dumps(data.get('attributes', {}), indent=2)}")
            
            # Check for remote
            remote_id = entity_id.replace("media_player", "remote")
            r2 = requests.get(f"{ha_url}/api/states/{remote_id}", headers=headers)
            if r2.status_code == 200:
                print(f"\nRemote {remote_id} found: {r2.json()['state']}")
            else:
                print(f"\nRemote {remote_id} NOT found.")
                
        else:
            print(f"Error: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"Ex: {e}")

if __name__ == "__main__":
    asyncio.run(main())
