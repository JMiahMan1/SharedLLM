
import os
import requests
import json
import logging

logging.basicConfig(level=logging.INFO)

HA_URL = os.environ.get("HA_URL", "http://ha.sumemail.com")
HA_TOKEN = os.environ.get("HA_TOKEN")

def main():
    if not HA_TOKEN:
        print("ERROR: HA_TOKEN not found.")
        return

    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }
    
    # 1. Get States to find entity
    print("Fetching States...")
    states_res = requests.get(f"{HA_URL}/api/states", headers=headers)
    states = states_res.json()
    
    office_tv_entity = next((s for s in states if s['entity_id'] == "media_player.office_tv_chrome"), None)
    
    if not office_tv_entity:
        print("Office TV Entity not found.")
        return

    print(f"Found Entity: {office_tv_entity['entity_id']}")
    
    # 2. To get IP/Mac, we usually need the Device Registry which is not open via public REST API easily.
    # However, sometimes it is in the 'attributes' or can be found via 'config/devices' in the websocket.
    # The REST API has /api/config/devices? No.
    # We can try to match via the 'context.id' or look at the entity attributes.
    
    # Let's verify what we have in attributes first (we saw it before, it lacked IP).
    
    # Advanced: If running inside HA container (which we aren't, we are in rag container), we could import.
    # But we are external. 
    # The user asks "Can you get IP... from Home Assistant?".
    # Answer: Via the standard REST API, it's hard if not in attributes.
    # We can try the template API to render: {{ state_attr('media_player.office_tv_chrome', 'ip_address') }}?
    pass

    # Let's try to render a template that might expose it if the integration provides it?
    tpl = '{{ state_attr("media_player.office_tv_chrome", "ip_address") }}' # Standard cast integration attribute?
    tpl_res = requests.post(f"{HA_URL}/api/template", headers=headers, json={"template": tpl})
    print(f"Template IP check: {tpl_res.text}")

if __name__ == "__main__":
    main()
