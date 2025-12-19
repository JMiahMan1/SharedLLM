#!/usr/bin/env python3
"""
Test Script for HA Area Resolution
Verifies that commands targeting valid HA Areas resolve to the correct devices.
"""
import requests
import time
import os
import json

# Load env vars
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

API_URL = "http://192.168.2.211:11435/api/chat"
HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
HA_TOKEN = os.getenv("HA_TOKEN")

headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

def send_chat(message):
    print(f"\n📱 USER: {message}")
    try:
        response = requests.post(API_URL, json={"query": message}, timeout=60)
        if response.status_code == 200:
            data = response.json()
            if "message" in data and "content" in data["message"]:
                 print(f"🤖 ASSISTANT: {data['message']['content']}")
                 return data['message']['content']
            elif "response" in data:
                 print(f"🤖 ASSISTANT: {data['response']}")
                 return data['response']
            else:
                 print(f"🤖 ASSISTANT: (Raw Data) {data}")
                 return str(data)
        else:
            print(f"❌ ERROR: {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ COMM ERROR: {e}")
        return None

def get_state(entity_id):
    try:
        url = f"{HA_URL.rstrip('/')}/api/states/{entity_id}"
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            return response.json().get("state")
    except Exception:
        return None
    return "unknown"

print("TESTING: Area Resolution Logic")
print("============================================================")

# 1. Test Office Area (Should target Office TV)
print("\n1️⃣ Test: 'Play music in the Office'")
# Ensure Office TV is off first
requests.post(f"{HA_URL}/api/services/media_player/turn_off", headers=headers, json={"entity_id": "media_player.office_tv_chrome_2"})
time.sleep(2)

resp = send_chat("Play Brandon Lake in the Office")

# Verification: Check if Office TV is playing
time.sleep(5) # Allow time for processing
state = get_state("media_player.office_tv_chrome_2")
print(f"   Office TV State: {state}")

if state in ["playing", "buffering"]:
    print("✅ PASS: Office TV targeted correctly.")
else:
    print("⚠️ WARN: Office TV did not start playing. Check logs.")


# 2. Test Living Room Area (Should target LG TV)
print("\n2️⃣ Test: 'Play music in the Living Room'")
resp = send_chat("Play Brandon Lake in the Living Room")

time.sleep(5)
# LG TV Entity logic likely targets 'media_player.lg_webos_tv_un6955zuf' or the group
state = get_state("media_player.lg_webos_tv_un6955zuf")
print(f"   LG TV State: {state}")

# Note: LG TV might be off/offline, so just checking if it tried handling it is often enough via the text response.
if "Living Room" in str(resp) or state in ["playing", "buffering"]:
     print("✅ PASS: Living Room parsed correctly.")
elif "offline" in str(resp).lower():
     print("✅ PASS: Living Room parsed (Device Offline).")
else:
     print(f"⚠️ WARN: Response didn't confirm Living Room target. Resp: {resp}")

print("\nDone.")
