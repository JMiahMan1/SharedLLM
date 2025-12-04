import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
HEADERS = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

def print_step(msg):
    print(f"\n{'='*40}\n{msg}\n{'='*40}")

if not HA_URL or not HA_TOKEN:
    print("ERROR: HA_URL or HA_TOKEN not set in .env")
    exit(1)

print_step(f"Connecting to Home Assistant at {HA_URL}...")

# 1. Test API Connectivity
try:
    resp = requests.get(f"{HA_URL.rstrip('/')}/api/", headers=HEADERS, timeout=5)
    if resp.status_code == 200:
        print(f"[PASS] API is reachable: {resp.json().get('message')}")
    else:
        print(f"[FAIL] API returned {resp.status_code}: {resp.text}")
except Exception as e:
    print(f"[FAIL] Connection Error: {e}")
    exit(1)

# 2. List Media Players
print_step("Scanning for Media Players...")
players = []
try:
    resp = requests.get(f"{HA_URL.rstrip('/')}/api/states", headers=HEADERS, timeout=5)
    if resp.status_code == 200:
        states = resp.json()
        for s in states:
            eid = s['entity_id']
            if eid.startswith("media_player."):
                state = s['state']
                fname = s.get('attributes', {}).get('friendly_name', eid)
                print(f" - {eid} ({fname}): {state}")
                if state not in ['unavailable', 'unknown']:
                    players.append(eid)
    else:
        print(f"[FAIL] Could not fetch states: {resp.status_code}")
except Exception as e:
    print(f"[FAIL] Error fetching states: {e}")

if not players:
    print("\n[WARN] No valid media players found! Alarms will fail.")
else:
    print(f"\n[OK] Found {len(players)} potential target(s).")
    target = players[4] # Pick first one for test
    
    # 3. Test TTS Playback
    print_step(f"Testing TTS on {target}...")
    payload = {
        "entity_id": target,
        "media_content_id": "This is a test of the alarm system audio.",
        "media_content_type": "text" 
    }
    try:
        url = f"{HA_URL.rstrip('/')}/api/services/media_player/play_media"
        resp = requests.post(url, headers=HEADERS, json=payload, timeout=10)
        print(f"Service Call Status: {resp.status_code}")
        print(f"Response: {resp.text}")
    except Exception as e:
        print(f"[FAIL] TTS Call Error: {e}")
