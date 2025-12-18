#!/usr/bin/env python3
"""
Proper end-to-end test with verification
"""
import requests
import time
import os

API_URL = "http://192.168.2.211:11435/api/chat"
HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
HA_TOKEN = os.getenv("HA_TOKEN")

headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

def get_tv_state():
    """Get the current state of the Office TV"""
    response = requests.get(
        f"{HA_URL}/api/states/media_player.office_tv_chrome",
        headers=headers,
        timeout=10
    )
    if response.status_code == 200:
        data = response.json()
        return {
            "state": data["state"],
            "app": data["attributes"].get("app_name", "N/A"),
            "media_title": data["attributes"].get("media_title", "N/A")
        }
    return None

def send_chat(message):
    """Send a chat message"""
    print(f"\n📱 USER: {message}")
    response = requests.post(
        API_URL,
        json={"query": message},
        timeout=30
    )
    
    if response.status_code == 200:
        data = response.json()
        print(f"🤖 ASSISTANT: {data.get('response', 'No response')[:100]}...")
        return True
    else:
        print(f"❌ ERROR: {response.status_code}")
        return False

print("="*60)
print("END-TO-END CASTING TEST WITH VERIFICATION")
print("="*60)

# Check initial state
print("\n1️⃣ Checking initial TV state...")
initial_state = get_tv_state()
if initial_state:
    print(f"   State: {initial_state['state']}")
    print(f"   App: {initial_state['app']}")
else:
    print("   ⚠️  Could not get TV state")

# Test 1: Play Big Buck Bunny
print("\n2️⃣ Playing Big Buck Bunny...")
if not send_chat("Play Big Buck Bunny video on the Office TV"):
    print("❌ Big Buck Bunny command failed")
    exit(1)

print("   ⏳ Waiting 8 seconds for playback to start...")
time.sleep(8)

bbb_state = get_tv_state()
if bbb_state:
    print(f"   ✅ TV State: {bbb_state['state']}")
    print(f"   ✅ App: {bbb_state['app']}")
    print(f"   ✅ Media: {bbb_state['media_title']}")
    if bbb_state['state'] not in ['playing', 'buffering']:
        print(f"   ⚠️  Expected 'playing', got '{bbb_state['state']}'")
else:
    print("   ❌ Could not verify Big Buck Bunny")

# Test 2: Switch to Fireplace
print("\n3️⃣ Switching to Fireplace...")
if not send_chat("Watch a fireplace video on the Office TV"):
    print("❌ Fireplace command failed")
    exit(1)

print("   ⏳ Waiting 8 seconds for new video to start...")
time.sleep(8)

fireplace_state = get_tv_state()
if fireplace_state:
    print(f"   ✅ TV State: {fireplace_state['state']}")
    print(f"   ✅ App: {fireplace_state['app']}")
    print(f"   ✅ Media: {fireplace_state['media_title']}")
    
    # Check if it actually switched
    if fireplace_state.get('media_title') != bbb_state.get('media_title'):
        print(f"   ✅ Successfully switched videos!")
    else:
        print(f"   ⚠️  Media title didn't change")
        
    if fireplace_state['state'] not in ['playing', 'buffering']:
        print(f"   ⚠️  Expected 'playing', got '{fireplace_state['state']}'")
else:
    print("   ❌ Could not verify Fireplace")

print("\n" + "="*60)
print("TEST SUMMARY")
print("="*60)
print(f"Initial: {initial_state['state'] if initial_state else 'Unknown'}")
print(f"After Big Buck Bunny: {bbb_state['state'] if bbb_state else 'Failed'}")
print(f"After Fireplace: {fireplace_state['state'] if fireplace_state else 'Failed'}")
print("="*60)
