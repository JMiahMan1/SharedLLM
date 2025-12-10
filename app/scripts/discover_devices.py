
import os
import sys
import requests
import json
from pprint import pprint

# Add parent directory to path to import settings
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from settings import HA_URL, HA_TOKEN

def detect_integration(entity):
    attrs = entity.get("attributes", {})
    eid = entity["entity_id"]
    
    if "music_assistant" in eid or "mass_player_id" in attrs:
        return "Music Assistant"
    
    if "adb_response" in attrs or "app_id" in attrs or "androidtv" in eid:
        return "Android TV"
        
    if "source_list" in attrs:
        # WebOS usually has sources like 'HDMI 1', 'Netflix', 'YouTube'
        # Roku also has source_list.
        # Check specific attributes
        if "sound_output" in attrs: # WebOS
            return "WebOS"
        if "roku" in eid:
            return "Roku"
        return "WebOS/SmartTV (Generic)"
    
    if "cast" in eid or "chrome" in eid:
        return "Google Cast"
        
    return "Unknown"

def main():
    print(f"Connecting to HA at {HA_URL}...")
    headers = {"Authorization": f"Bearer {HA_TOKEN}"}
    
    try:
        resp = requests.get(f"{HA_URL}/api/states", headers=headers)
        resp.raise_for_status()
        states = resp.json()
    except Exception as e:
        print(f"Error fetching states: {e}")
        return

    players = [s for s in states if s["entity_id"].startswith("media_player.")]
    
    print(f"\nFound {len(players)} Media Players:\n")
    print(f"{'ENTITY ID':<40} | {'STATE':<10} | {'INTEGRATION':<20} | {'FRIENDLY NAME'}")
    print("-" * 100)
    
    living_room_matches = []
    
    for p in players:
        eid = p["entity_id"]
        state = p["state"]
        attrs = p.get("attributes", {})
        fname = attrs.get("friendly_name", "Unknown")
        
        integ = detect_integration(p)
        
        print(f"{eid:<40} | {state:<10} | {integ:<20} | {fname}")
        
        if "living" in eid.lower() or "living" in fname.lower():
            living_room_matches.append(p)
            
    # Suggestions for Living Room
    if living_room_matches:
        print("\n--- Living Room Candidates ---")
        for m in living_room_matches:
            print(f"ID: {m['entity_id']} ({detect_integration(m)})")

if __name__ == "__main__":
    main()
