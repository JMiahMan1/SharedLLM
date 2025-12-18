
import os
import requests
import json
import logging

logging.basicConfig(level=logging.INFO)

HA_URL = os.environ.get("HA_URL", "http://ha.sumemail.com")
HA_TOKEN = os.environ.get("HA_TOKEN")

TARGET_MAC = "30:95:87:15:E7:6D".lower()
TARGET_IP = "192.168.2.148" # From our scan earlier, assuming 192.168.2.x range, wait, we found 159, 238, 240. 
# We actually never confirmed WHICH one was office TV in the debug script because it failed to connect/auth properly or I missed the log output in the final run.
# The user provided MAC 30:95... 

def normalize_mac(mac):
    if not mac: return ""
    return mac.replace(":", "").replace("-", "").lower()

def main():
    if not HA_TOKEN:
        print("ERROR: HA_TOKEN not found.")
        return

    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }
    
    print(f"DEBUG: Using HA_URL={HA_URL}")
    print("Fetching all states...")
    try:
        res = requests.get(f"{HA_URL}/api/states", headers=headers, timeout=10)
        res.raise_for_status()
        states = res.json()
    except Exception as e:
        print(f"Failed to fetch states: {e}")
        return

    found = False
    print(f"Scanning {len(states)} entities for MAC {TARGET_MAC}...")
    
    for entity in states:
        attrs = entity.get('attributes', {})
        eid = entity['entity_id']
        
        # Check for Mac match
        # Attributes might be 'mac', 'mac_address', 'wifi_mac', etc.
        # Recursively search values? Or just common keys.
        
        matched_mac = False
        for k, v in attrs.items():
            if isinstance(v, str) and normalize_mac(v) == normalize_mac(TARGET_MAC):
                matched_mac = True
                print(f"[MATCH MAC] Found in {eid} attribute '{k}': {v}")
                
        # Check for IP match (if we knew it) - We don't verify IP yet, but let's see if we find any device_trackers for "office_tv"
        if "office" in eid.lower() and "tv" in eid.lower():
             print(f"[POTENTIAL] {eid} (State: {entity['state']}) Attributes: {json.dumps(attrs)}")

        if matched_mac:
            found = True
            
    if not found:
        print("No exact MAC match found in any entity attributes.")

if __name__ == "__main__":
    main()
