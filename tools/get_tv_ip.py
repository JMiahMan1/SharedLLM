
import os
import requests
import json
import logging

logging.basicConfig(level=logging.INFO)

HA_URL = os.environ.get("HA_URL", "http://ha.sumemail.com")
HA_TOKEN = os.environ.get("HA_TOKEN")
ENTITY_ID = "media_player.office_tv_chrome"

def main():
    if not HA_TOKEN:
        print("ERROR: HA_TOKEN not found in env.")
        return

    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }
    
    url = f"{HA_URL}/api/states/{ENTITY_ID}"
    print(f"Fetching: {url}")
    
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        print(json.dumps(data, indent=2))
        
        # Try to find IP (sometimes hidden in attributes)
        # Usually friendly_name is there. IP might not be exposed by HA API directly 
        # for cast entities unless extended attrs are enabled or we infer it.
        # But let's see what we get.
        
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    main()
