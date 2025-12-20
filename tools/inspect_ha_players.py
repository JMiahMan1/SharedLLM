import os
import sys
import requests
import json

import argparse

# Add project root to path
sys.path.append(os.getcwd())

parser = argparse.ArgumentParser(description='Inspect HA Media Players')
parser.add_argument('--filter', type=str, help='Filter entities by ID or name')
parser.add_argument('--attributes', action='store_true', help='Show full attributes')
args = parser.parse_args()

try:
    from app import settings
    # Initialize global resources to ensure settings are loaded if needed
    # settings.load_resources() # Might be async, skip
    credentials = settings.get_user_creds("admin")
    HA_URL = settings.HA_URL
    HA_TOKEN = credentials.get("ha_token")
except Exception as e:
    print(f"Error loading settings: {e}")
    # Fallback to env vars if strict import fails
    HA_URL = os.getenv("HA_URL")
    HA_TOKEN = os.getenv("HA_TOKEN")

if not HA_URL or not HA_TOKEN:
    print("FATAL: HA_URL or HA_TOKEN not available.")
    sys.exit(1)

print(f"Querying {HA_URL}...")
headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

try:
    resp = requests.get(f"{HA_URL}/api/states", headers=headers, timeout=10)
    resp.raise_for_status()
    states = resp.json()
except Exception as e:
    print(f"Request failed: {e}")
    sys.exit(1)

print(f"Total Entities: {len(states)}")
print("=" * 60)

count = 0
for entity in states:
    eid = entity['entity_id']
    if not eid.startswith("media_player."):
        continue
        
    attrs = entity.get('attributes', {})
    friendly_name = attrs.get('friendly_name', '')
    
    # Filter logic
    if args.filter:
        if args.filter.lower() not in eid.lower() and args.filter.lower() not in friendly_name.lower():
            continue
    else:
        # Default legacy filter
        mass_type = attrs.get('mass_player_type')
        active_queue = attrs.get('active_queue')
        if not (mass_type or active_queue or "_chrome" in eid):
            continue

    count += 1
    print(f"ID: {eid}")
    print(f"  Friendly Name: {friendly_name}")
    print(f"  State: {entity['state']}")
    
    if args.attributes:
        print("  Attributes:")
        print(json.dumps(attrs, indent=4))
    else:
        mass_type = attrs.get('mass_player_type')
        if mass_type:
            print(f"  [MASS] Type: {mass_type}")
        active_queue = attrs.get('active_queue')
        if active_queue:
            print(f"  [LINK] Active Queue Of: {active_queue}")
            
    print("-" * 40)

print(f"Found {count} relevant media players.")
