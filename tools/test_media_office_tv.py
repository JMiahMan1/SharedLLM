#!/usr/bin/env python3
"""
Test Script for Office TV (Android/Cast)
"""
import requests
import time
import os
import sys

API_URL = "http://192.168.2.211:11435/api/chat"
HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
HA_TOKEN = os.getenv("HA_TOKEN")
DEVICE_NAME = "Office TV"
ENTITY_ID = "media_player.office_tv_chrome_2"

headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

def get_tv_state():
    try:
        response = requests.get(
            f"{HA_URL}/api/states/{ENTITY_ID}",
            headers=headers,
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            attrs = data.get("attributes", {})
            return {
                "state": data["state"],
                "app": attrs.get("app_name", "N/A"),
                "media_title": attrs.get("media_title", "N/A"),
                "volume": attrs.get("volume_level", "N/A")
            }
    except Exception as e:
        print(f"Error fetching state: {e}")
    return None

def send_chat(message):
    print(f"\n📱 USER: {message}")
    try:
        response = requests.post(
            API_URL,
            json={"query": message},
            timeout=60
        )
        if response.status_code == 200:
            data = response.json()
            print(f"🤖 ASSISTANT: {data.get('response', 'No response')}")
            return True
        else:
            print(f"❌ ERROR: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"❌ COMM ERROR: {e}")
        return False

print(f"TESTING: {DEVICE_NAME} ({ENTITY_ID})")
print("="*60)

# 1. Initial State
print("\n1️⃣ Checking Initial State...")
state = get_tv_state()
if state:
    print(f"   State: {state['state']} | Media: {state['media_title']}")
else:
    print("   ⚠️ Could not fetch state")

# 2. Music Test
print("\n2️⃣ Testing Music Playback (Brandon Lake)...")
send_chat(f"Play Brandon Lake on the {DEVICE_NAME}")
time.sleep(10)
state = get_tv_state()
print(f"   State: {state['state'] if state else 'Unknown'} | Media: {state['media_title'] if state else 'Unknown'}")

# 3. Video Test
print("\n3️⃣ Testing Video Playback (Big Buck Bunny)...")
send_chat(f"Play Big Buck Bunny video on the {DEVICE_NAME}")
time.sleep(15)
state = get_tv_state()
print(f"   State: {state['state'] if state else 'Unknown'} | Media: {state['media_title'] if state else 'Unknown'}")

# 4. Control Tests
print("\n4️⃣ Testing Controls (Pause)...")
send_chat(f"Pause the {DEVICE_NAME}")
time.sleep(5)
state = get_tv_state()
print(f"   State: {state['state'] if state else 'Unknown'}")

print("\n5️⃣ Testing Controls (Resume)...")
send_chat(f"Resume the {DEVICE_NAME}")
time.sleep(5)
state = get_tv_state()
print(f"   State: {state['state'] if state else 'Unknown'}")

print("\n6️⃣ Testing Controls (Stop)...")
send_chat(f"Stop the {DEVICE_NAME}")
time.sleep(5)
state = get_tv_state()
print(f"   State: {state['state'] if state else 'Unknown'}")

print("\n✅ Test Complete")
