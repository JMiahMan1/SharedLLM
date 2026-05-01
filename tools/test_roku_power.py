import os
import sys
import requests
import json
import time

# Add project root to path
sys.path.append(os.getcwd())

# Configuration
MEDIA_PLAYER_ID = "media_player.28_tcl_roku_tv"
REMOTE_ID = "remote.28_tcl_roku_tv"
HA_URL = os.getenv("HA_URL", "http://192.168.2.205:8123") # Default internal if env missing
HA_TOKEN = os.getenv("HA_TOKEN")

if not HA_TOKEN:
    print("FATAL: HA_TOKEN not set.")
    sys.exit(1)

headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

def get_state(entity_id):
    try:
        resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return data['state'], data.get('attributes', {})
    except Exception as e:
        print(f"Error fetching state for {entity_id}: {e}")
        return "unknown", {}

def call_service(domain, service, data):
    url = f"{HA_URL}/api/services/{domain}/{service}"
    try:
        print(f"🔄 Calling {domain}.{service} with {data}...")
        resp = requests.post(url, headers=headers, json=data, timeout=10)
        resp.raise_for_status()
        print("   ✅ Service called successfully.")
        return True
    except Exception as e:
        print(f"   ❌ Service call failed: {e}")
        return False

print(f"TESTING ROKU POWER: {MEDIA_PLAYER_ID}")
print("=" * 60)

# 1. Initial State
print("\n1️⃣ Checking Initial State...")
mp_state, mp_attrs = get_state(MEDIA_PLAYER_ID)
rem_state, rem_attrs = get_state(REMOTE_ID)
print(f"   Media Player State: {mp_state}")
print(f"   Remote State:       {rem_state}")

if mp_state == "on" or mp_state == "idle" or mp_state == "playing":
    print("\n   ⚠️ Roku is ALREADY ON. Turning it OFF for test...")
    call_service("media_player", "turn_off", {"entity_id": MEDIA_PLAYER_ID})
    time.sleep(5)
    mp_state, _ = get_state(MEDIA_PLAYER_ID)
    print(f"   New State: {mp_state}")
    if mp_state != "off" and mp_state != "standby":
         print("   ❌ Failed to turn off. Proceeding anyway, but test validity is reduced.")

# 2. Test A: Standard media_player.turn_on
print("\n2️⃣ Test A: Standard media_player.turn_on")
call_service("media_player", "turn_on", {"entity_id": MEDIA_PLAYER_ID})
print("   ⏳ Waiting 10s for wake up...")
time.sleep(10)
mp_state, _ = get_state(MEDIA_PLAYER_ID)
print(f"   State after turn_on: {mp_state}")

if mp_state in ["on", "idle", "playing", "home"]:
    print("   🎉 SUCCESS! Standard turn_on works.")
else:
    print("   ❌ FAILED. Standard turn_on did not wake device.")

    # 3. Test B: Remote Send Command (Home)
    print("\n3️⃣ Test B: Remote Send 'Home' Key")
    call_service("remote", "send_command", {
        "entity_id": REMOTE_ID,
        "command": "home"
    })
    print("   ⏳ Waiting 10s...")
    time.sleep(10)
    mp_state, _ = get_state(MEDIA_PLAYER_ID)
    print(f"   State after 'home': {mp_state}")
    
    if mp_state in ["on", "idle", "playing", "home"]:
        print("   🎉 SUCCESS! 'Home' key woke the device.")
    else:
        print("   ❌ FAILED. 'Home' key did not wake device.")

        # 4. Test C: Remote Send Command (PowerOn)
        print("\n4️⃣ Test C: Remote Send 'PowerOn' Key")
        call_service("remote", "send_command", {
            "entity_id": REMOTE_ID,
            "command": "poweron" # Sometimes 'power' or 'PowerOn'
        })
        print("   ⏳ Waiting 10s...")
        time.sleep(10)
        mp_state, _ = get_state(MEDIA_PLAYER_ID)
        print(f"   State after 'poweron': {mp_state}")
        
        if mp_state in ["on", "idle", "playing", "home"]:
            print("   🎉 SUCCESS! 'PowerOn' key woke the device.")
        else:
             print("   ❌ FAILED. All methods exhausted.")

print("\nDONE.")
