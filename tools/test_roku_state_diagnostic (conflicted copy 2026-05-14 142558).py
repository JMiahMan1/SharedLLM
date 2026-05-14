#!/usr/bin/env python3
"""
Roku State Change Diagnostic
Tests if we can detect ANY state changes on the Roku
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

def get_full_state(entity_id):
    """Get complete entity state including all attributes"""
    try:
        resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=5)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None

print("=" * 70)
print("ROKU STATE DIAGNOSTIC - Looking for ANY state changes")
print("=" * 70)

# Get initial full state
print("\n📊 Initial Full State:")
initial = get_full_state(MEDIA_PLAYER)
if initial:
    print(f"  State: {initial.get('state')}")
    print(f"  Last Changed: {initial.get('last_changed')}")
    print(f"  Last Updated: {initial.get('last_updated')}")
    attrs = initial.get('attributes', {})
    print(f"  App: {attrs.get('app_name', 'N/A')}")
    print(f"  App ID: {attrs.get('app_id', 'N/A')}")
    print(f"  Media Title: {attrs.get('media_title', 'N/A')}")

# Try turn_off first (to see if state changes TO off)
print("\n🔴 Testing: media_player.turn_off")
resp = requests.post(
    f"{HA_URL}/api/services/media_player/turn_off",
    headers=headers,
    json={"entity_id": MEDIA_PLAYER},
    timeout=10
)
print(f"  API Response: {resp.status_code}")
time.sleep(5)

after_off = get_full_state(MEDIA_PLAYER)
if after_off:
    print(f"  State: {initial.get('state')} → {after_off.get('state')}")
    print(f"  Last Changed: {after_off.get('last_changed')}")
    if initial.get('last_changed') != after_off.get('last_changed'):
        print("  ✅ State timestamp CHANGED - HA is receiving updates!")
    else:
        print("  ⚠️ State timestamp UNCHANGED - HA may not be communicating with device")

# Now try turn_on
print("\n🟢 Testing: media_player.turn_on")
resp = requests.post(
    f"{HA_URL}/api/services/media_player/turn_on",
    headers=headers,
    json={"entity_id": MEDIA_PLAYER},
    timeout=10
)
print(f"  API Response: {resp.status_code}")
time.sleep(10)

after_on = get_full_state(MEDIA_PLAYER)
if after_on:
    print(f"  State: {after_off.get('state')} → {after_on.get('state')}")
    print(f"  Last Changed: {after_on.get('last_changed')}")
    if after_off.get('last_changed') != after_on.get('last_changed'):
        print("  ✅ State timestamp CHANGED - HA is receiving updates!")
    else:
        print("  ⚠️ State timestamp UNCHANGED - HA may not be communicating with device")

print("\n" + "=" * 70)
print("🎯 DIAGNOSIS:")
if initial and after_off and after_on:
    if (initial.get('last_changed') == after_off.get('last_changed') == after_on.get('last_changed')):
        print("  ❌ State NEVER changed - HA cannot communicate with Roku")
        print("  Possible causes:")
        print("     - Roku integration not loaded in HA")
        print("     - Network issues between HA and Roku")
        print("     - Roku device offline/unreachable")
    else:
        print("  ✅ State CAN change - Communication working")
        print("     Need to find correct power-on method")
print("=" * 70)
