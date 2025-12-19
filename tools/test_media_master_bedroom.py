#!/usr/bin/env python3
"""
Test Script for Master Bedroom TV (Chromecast)
"""
import requests
import time
import os

API_URL = "http://192.168.2.211:11435/api/chat"
HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
HA_TOKEN = os.getenv("HA_TOKEN")
DEVICE_NAME = "Master Bedroom TV"
ENTITY_ID = "media_player.master_bedroom_tv_2"  # Using MASS wrapper for verification

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

print(f"TESTING: {DEVICE_NAME} ({ENTITY_ID})")
print("="*60)

print("\n1️⃣ Checking Initial State...")
state = get_tv_state()
print(f"   State: {state['state'] if state else 'Unknown'}")

print("\n2️⃣ Testing Music Playback (Brandon Lake)...")
send_chat(f"Play Brandon Lake on the {DEVICE_NAME}")
time.sleep(10)
state = get_tv_state()
print(f"   State: {state['state'] if state else 'Unknown'} | Media: {state.get('media_title', 'N/A') if state else 'N/A'}")

print("\n3️⃣ Testing Video Playback (Big Buck Bunny)...")
send_chat(f"Play Big Buck Bunny video on the {DEVICE_NAME}")
time.sleep(15)
state = get_tv_state()
print(f"   State: {state['state'] if state else 'Unknown'} | Media: {state.get('media_title', 'N/A') if state else 'N/A'}")

print("\n4️⃣ Testing Stop...")
send_chat(f"Stop the {DEVICE_NAME}")
time.sleep(5)
state = get_tv_state()
print(f"   State: {state['state'] if state else 'Unknown'}")
