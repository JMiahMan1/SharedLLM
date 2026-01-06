import requests
import time
import sys
import xml.etree.ElementTree as ET

# CONFIG
ROKU_IP = "192.168.2.166"
SERVER_IP = "192.168.2.211"
SERVER_PORT = "11435"
VIDEO_FILE = "1c504eb5640b.mp4"

# Roku Media Player ID
CHANNEL_ID = "2213" 

VIDEO_URL = f"http://{SERVER_IP}:{SERVER_PORT}/cast_video/{VIDEO_FILE}"

print("="*40)
print("Roku Media Player (2213) Launch Test")
print("="*40)
print(f"Target: {ROKU_IP}")

# ECP Parameters for Roku Media Player
# Documented params often include:
# - contentId (URL or DLNA ID)
# - mediaType ('movie', 'live', 'audio', 'photo')
# - u (URL - older param)
params = {
    "contentId": VIDEO_URL,
    "mediaType": "movie"
    # "u": VIDEO_URL # Try adding this if the above fails
}

url = f"http://{ROKU_IP}:8060/launch/{CHANNEL_ID}"

print(f"\nLaunching Channel {CHANNEL_ID}...")
print(f"   URL: {url}")
print(f"   Params: {params}")

try:
    resp = requests.post(url, params=params, timeout=5)
    print(f"   Response: {resp.status_code}")
    
    if resp.status_code == 200:
        print("\n✅ Command sent successfully!")
        print("   Watch the TV...")
    else:
        print(f"❌ Failed: {resp.text}")
        sys.exit(1)

except Exception as e:
    print(f"❌ Exception: {e}")
    sys.exit(1)

print("\nMonitoring active app state...")
for i in range(10):
    try:
        r = requests.get(f"http://{ROKU_IP}:8060/query/active-app", timeout=2)
        print(f"   [{i*2}s] Active App: {r.text.replace(chr(10), '').replace(chr(9), ' ')[0:80]}...")
    except:
        pass
    time.sleep(2)
