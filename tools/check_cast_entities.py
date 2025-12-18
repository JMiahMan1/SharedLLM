import os
import requests

HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
HA_TOKEN = os.getenv("HA_TOKEN")

headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

# Check both entities
entities = [
    "media_player.office_tv_chrome",
    "media_player.office_tv_chrome_2"
]

for entity_id in entities:
    print(f"\n{'='*60}")
    print(f"Entity: {entity_id}")
    print('='*60)
    
    response = requests.get(
        f"{HA_URL}/api/states/{entity_id}",
        headers=headers,
        timeout=10
    )
    
    if response.status_code == 200:
        data = response.json()
        print(f"Friendly Name: {data['attributes'].get('friendly_name')}")
        print(f"State: {data['state']}")
        print(f"Supported Features: {data['attributes'].get('supported_features')}")
        print(f"App Name: {data['attributes'].get('app_name', 'N/A')}")
        print(f"Integration: {data.get('entity_id', '').split('.')[0]}")
        
        # Check if it's a Music Assistant wrapper
        if 'mass_player_type' in data['attributes']:
            print(f"⚠️  Music Assistant Player (Wrapper)")
            print(f"   Active Queue: {data['attributes'].get('active_queue', 'N/A')}")
        else:
            print(f"✅ Direct Cast Device")
    else:
        print(f"Error: {response.status_code}")
