import requests

API_URL = "http://ai.local:11435/api/ha/states"

def scan_devices():
    try:
        print(f"Scanning {API_URL}...")
        resp = requests.get(API_URL, timeout=10)
        resp.raise_for_status()
        states = resp.json()
        
        lights = []
        media = []
        
        for state in states:
            eid = state['entity_id']
            name = state['attributes'].get('friendly_name', eid)
            
            if eid.startswith("light."):
                lights.append(f"{eid} ({name})")
            elif eid.startswith("media_player."):
                media.append(f"{eid} ({name})")
                
        print("\n--- Available Lights ---")
        for l in sorted(lights):
            print(l)
            
        print("\n--- Available Media Players ---")
        for m in sorted(media):
            print(m)
            
    except Exception as e:
        print(f"Error scanning: {e}")

if __name__ == "__main__":
    scan_devices()
