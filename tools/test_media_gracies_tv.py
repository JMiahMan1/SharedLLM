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
DEVICE_NAME = "Gracies TV"
ENTITY_ID = "media_player.roku_2n0062385487"

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
                "media_title": attrs.get("media_title", "N/A")
            }
    except: pass
    return None

def send_chat(message):
    print(f"\n📱 USER: {message}")
    try:
        response = requests.post(API_URL, json={"query": message}, timeout=60)
        if response.status_code == 200:
            print(f"🤖 ASSISTANT: {response.json().get('response', 'No response')}")
        else:
            print(f"❌ ERROR: {response.status_code}")
    except Exception as e:
        print(f"❌ COMM ERROR: {e}")

print(f"TESTING: {DEVICE_NAME} ({ENTITY_ID})")
print("="*60)

print("\n1️⃣ Checking Initial State...")
state = get_tv_state()
print(f"   State: {state['state'] if state else 'Unknown'}")

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
