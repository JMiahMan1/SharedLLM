#!/usr/bin/env python3
"""
Test Script for Gracies TV (Roku)
"""
import requests
import time
import os

API_URL = "http://192.168.2.211:11435/api/chat"
HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
HA_TOKEN = os.getenv("HA_TOKEN")
# Device Configuration
# TARGET_ENTITY = "media_player.roku_2n0062385487" # OLD (MASS)
TARGET_ENTITY = "media_player.28_tcl_roku_tv" # NEW (Native Roku)
DEVICE_NAME = "Gracies TV"

headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

REMOTE_ENTITY = "remote.28_tcl_roku_tv"

def get_entity_state(entity_id):
    try:
        url = f"{HA_URL}/api/states/{entity_id}"
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            return resp.json().get("state", "unknown")
    except:
        pass
    return "unknown"

def get_tv_state():
    try:
        url = f"{HA_URL}/api/states/{TARGET_ENTITY}"
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            attrs = data.get("attributes", {})
            return {
                "state": data.get("state", "Unknown"),
                "app": attrs.get("app_name", "N/A"),
                "media_title": attrs.get("media_title", "N/A")
            }
        else:
             print(f"   ⚠️ HA Error: {response.status_code} - {response.text}")
             return {"state": f"Error {response.status_code}"}
    except Exception as e:
        print(f"   ⚠️ Connection Error: {e}")
        return {"state": "Conn Error"}

def send_chat(message):
    print(f"\n📱 USER: {message}")
    try:
        response = requests.post(API_URL, json={"query": message}, timeout=60)
        if response.status_code == 200:
            data = response.json()
            if "message" in data and "content" in data["message"]:
                 print(f"🤖 ASSISTANT: {data['message']['content']}")
            elif "response" in data:
                 print(f"🤖 ASSISTANT: {data['response']}")
            else:
                 print(f"🤖 ASSISTANT: (Raw Data) {data}")
        else:
            print(f"❌ ERROR: {response.status_code}")
    except Exception as e:
        print(f"❌ COMM ERROR: {e}")

print(f"TESTING: {DEVICE_NAME} ({TARGET_ENTITY})")
print("="*60)

print("\n1️⃣ Checking & Ensuring Power State...")

def wait_for_power_on(timeout=30):
    start_time = time.time()
    print(f"   Waiting for device to turn ON (Timeout: {timeout}s)...")
    while time.time() - start_time < timeout:
        # Check both Media Player and Remote
        mp_state_data = get_tv_state() # Returns dict
        mp_state = mp_state_data.get("state", "unknown")
        
        rem_state = get_entity_state(REMOTE_ENTITY)
        
        print(f"   [Polling] MP: {mp_state} | Remote: {rem_state}", end="\r")
        
        # Criteria: Remote is 'on' OR Media Player is active
        if rem_state == "on" or mp_state in ["on", "idle", "playing", "paused", "home"]:
            print(f"\n   ✅ Device is ON (Remote: {rem_state}, MP: {mp_state})")
            return True
        
        # If off, send turn_on to BOTH to be sure
        if time.time() - start_time > 5: # Give it a few seconds before spamming
             requests.post(f"{HA_URL}/api/services/media_player/turn_on", headers=headers, json={"entity_id": TARGET_ENTITY})
             # Also try remote command if needed, but MP turn_on worked in test_roku_power
        
        time.sleep(2)
        
    print(f"\n   ❌ Failed to turn on device after {timeout} seconds.")
    return False

if not wait_for_power_on():
    print("⛔ CRITICAL: Device failed to turn on. Aborting test to prevent false positives.")
    exit(1)

state = get_tv_state()
print(f"   Current State: {state['state']}")

print("\n2️⃣ Testing Music Playback (Brandon Lake)...")
send_chat(f"Play Brandon Lake on {DEVICE_NAME}")
time.sleep(10)
state = get_tv_state()
print(f"   State: {state['state'] if state else 'Unknown'} | Media: {state.get('media_title', 'N/A') if state else 'N/A'}")

print("\n3️⃣ Testing Video Playback (Big Buck Bunny)...")
# Note: Roku might use 'RokuCast' or native YouTube app logic
send_chat(f"Play Big Buck Bunny video on {DEVICE_NAME}")
time.sleep(15)
state = get_tv_state()
print(f"   State: {state['state'] if state else 'Unknown'} | Media: {state.get('media_title', 'N/A') if state else 'N/A'}")

print("\n4️⃣ Testing Stop...")
send_chat(f"Stop {DEVICE_NAME}")
time.sleep(5)
state = get_tv_state()
print(f"   State: {state['state'] if state else 'Unknown'}")
