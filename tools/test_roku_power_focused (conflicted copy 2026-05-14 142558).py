#!/usr/bin/env python3
"""
Focused Roku Power Control Test
Tests multiple methods to turn on the TV and verifies actual state changes
"""
import requests
import os
import time

HA_URL = os.getenv("HA_URL", "https://localhost:8123")
HA_TOKEN = os.getenv("HA_TOKEN")
MEDIA_PLAYER = "media_player.28_tcl_roku_tv"
REMOTE = "remote.28_tcl_roku_tv"

headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

def get_state(entity_id):
    """Get current state of an entity"""
    try:
        resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("state"), data.get("attributes", {})
        return None, {}
    except Exception as e:
        print(f"Error getting state: {e}")
        return None, {}

def call_service(domain, service, entity_id, data=None):
    """Call a Home Assistant service"""
    url = f"{HA_URL}/api/services/{domain}/{service}"
    payload = {"entity_id": entity_id}
    if data:
        payload.update(data)
    
    try:
        print(f"  → Calling {domain}.{service} on {entity_id}")
        if data:
            print(f"     Data: {data}")
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"     Status: {resp.status_code}")
        return resp.status_code == 200
    except Exception as e:
        print(f"     Error: {e}")
        return False

print("=" * 70)
print("ROKU POWER CONTROL TEST")
print("=" * 70)

# Check initial state
print("\n📊 Initial State Check:")
mp_state, mp_attrs = get_state(MEDIA_PLAYER)
remote_state, remote_attrs = get_state(REMOTE)
print(f"  Media Player: {mp_state}")
print(f"  Remote: {remote_state}")

# Method 1: media_player.turn_on
print("\n🔌 Method 1: media_player.turn_on")
call_service("media_player", "turn_on", MEDIA_PLAYER)
print("  Waiting 10 seconds...")
time.sleep(10)
new_state, _ = get_state(MEDIA_PLAYER)
print(f"  New State: {mp_state} → {new_state}")

if new_state in ["on", "idle", "home", "playing"]:
    print("  ✅ SUCCESS! TV appears to be ON")
else:
    print("  ❌ TV still appears OFF, trying Method 2...")
    
    # Method 2: remote.send_command Home
    print("\n🎮 Method 2: remote.send_command (Home)")
    call_service("remote", "send_command", REMOTE, {"command": "Home"})
    print("  Waiting 10 seconds...")
    time.sleep(10)
    new_state, _ = get_state(MEDIA_PLAYER)
    print(f"  New State: {mp_state} → {new_state}")
    
    if new_state in ["on", "idle", "home", "playing"]:
        print("  ✅ SUCCESS! TV appears to be ON")
    else:
        print("  ❌ Still OFF, trying Method 3...")
        
        # Method 3: remote.send_command PowerOn
        print("\n⚡ Method 3: remote.send_command (PowerOn)")
        call_service("remote", "send_command", REMOTE, {"command": "PowerOn"})
        print("  Waiting 10 seconds...")
        time.sleep(10)
        new_state, _ = get_state(MEDIA_PLAYER)
        print(f"  New State: {mp_state} → {new_state}")
        
        if new_state in ["on", "idle", "home", "playing"]:
            print("  ✅ SUCCESS! TV appears to be ON")
        else:
            print("  ❌ All methods failed")

# Final state
print("\n📊 Final State:")
final_mp, _ = get_state(MEDIA_PLAYER)
final_remote, _ = get_state(REMOTE)
print(f"  Media Player: {mp_state} → {final_mp}")
print(f"  Remote: {remote_state} → {final_remote}")

print("\n" + "=" * 70)
print("🔍 PLEASE PHYSICALLY VERIFY:")
print("   Is the TV actually ON with display showing?")
print("=" * 70)
