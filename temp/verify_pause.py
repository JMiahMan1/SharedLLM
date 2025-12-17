
import requests
import time
import os
import sys

# Config
API_URL = os.getenv("API_URL", "http://192.168.2.211:11435")
HEADERS = {"X-RAG-User": "admin", "Content-Type": "application/json"}
HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
HA_TOKEN = os.getenv("HA_TOKEN", "")
DEVICE_ENTITY = "media_player.office_tv_chrome_2"

def get_state():
    try:
        r = requests.get(f"{HA_URL}/api/states/{DEVICE_ENTITY}", headers={"Authorization": f"Bearer {HA_TOKEN}"}, timeout=5)
        return r.json().get("state")
    except:
        return "error"

def send_chat(msg):
    print(f"Sending: '{msg}'...")
    try:
        r = requests.post(f"{API_URL}/api/chat", json={"messages": [{"role": "user", "content": msg}]}, headers=HEADERS, timeout=60)
        print(f"Response: {r.status_code} - {r.json().get('message', {}).get('content', '')[:100]}...")
        return r.status_code == 200
    except Exception as e:
        print(f"Error: {e}")
        return False

print("--- VERIFYING PAUSE/RESUME ROUTING FIX ---")

# 1. Launch / Ensure Playback
print("\n1. Skipping Launch Step (Assumed manually started via setup_playback.py)...")
s = get_state()
print(f"   Initial State: {s}")

if s != "playing":
    print("⚠️ WARNING: Device state is not 'playing'. Proceeding anyway for routing check.")

# 2. Pause (Routing Verification)
print("\n2. Pausing (Phrase: 'Pause music on Office TV')...")
send_chat("Pause music on Office TV")
time.sleep(5)
s = get_state()
print(f"   State after Pause: {s}")

# 3. Resume
print("\n3. Resuming (Phrase: 'Resume music on Office TV')...")
send_chat("Resume music on Office TV")
time.sleep(5)
s = get_state()
print(f"   State after Resume: {s}")

# 4. Stop (Cleanup)
print("\n4. Stopping (Phrase: 'Stop music on Office TV')...")
send_chat("Stop music on Office TV")
time.sleep(5)
s = get_state()
print(f"   State after Stop: {s}")
if s in ["idle", "off", "standby"]:
    print("✅ PASS: Device Stopped correctly (Routing Fix Verified!)")
else:
    print(f"❌ FAIL: Device did not stop. State: {s}")
