import os
import sys
import requests
import json

# Add project root to path
sys.path.append(os.getcwd())

try:
    from app import settings
    credentials = settings.get_user_creds("admin")
    HA_URL = settings.HA_URL
    HA_TOKEN = credentials.get("ha_token")
except Exception as e:
    HA_URL = os.getenv("HA_URL")
    HA_TOKEN = os.getenv("HA_TOKEN")

if not HA_URL or not HA_TOKEN:
    print("FATAL: HA_URL or HA_TOKEN not available.")
    sys.exit(1)

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

print(f"Searching for remote entities...")
found = 0
for entity in states:
    eid = entity['entity_id']
    if not eid.startswith("remote."):
        continue
    
    friendly = entity.get('attributes', {}).get('friendly_name', '').lower()
    if "roku" in eid.lower() or "gracie" in friendly or "tcl" in friendly:
        print(f"ID: {eid}")
        print(f"  Friendly Name: {entity.get('attributes', {}).get('friendly_name')}")
        print(f"  State: {entity['state']}")
        print("-" * 40)
        found += 1

print(f"Found {found} potential remotes.")
