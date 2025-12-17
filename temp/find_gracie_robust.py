
import sys
import os
sys.path.insert(0, os.getcwd())
# Load environment if not already loaded, though app.settings does this
from dotenv import load_dotenv
load_dotenv()

from app import settings
import requests
import json

def find_gracie():
    url = settings.HA_URL
    token = settings.HA_ENV_TOKEN
    
    if not url or not token:
        print("Error: HA_URL or HA_ENV_TOKEN not found in settings/env")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    try:
        print(f"Querying {url}/api/states...")
        response = requests.get(f"{url}/api/states", headers=headers, timeout=10)
        response.raise_for_status()
        
        entities = response.json()
        found = []
        for entity in entities:
            eid = entity['entity_id']
            name = entity.get('attributes', {}).get('friendly_name', '')
            state = entity['state']
            
            if 'media_player' in eid and 'gracie' in (eid + name).lower():
                found.append(f"{eid} | {name} | {state}")
                
        print("\nFound Entities:")
        if found:
            for item in found:
                print(item)
        else:
            print("No media_player entities found matching 'gracie'")
            
    except Exception as e:
        print(f"Error querying HA: {e}")

if __name__ == "__main__":
    find_gracie()
