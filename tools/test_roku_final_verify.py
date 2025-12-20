#!/usr/bin/env python3
"""
Final verification test for Roku Playback using user-provided parameters.
"""
import requests
import time
import sys

ROKU_IP = "192.168.2.166"
# Using the cached video we verified exists on the server
VIDEO_URL = "http://192.168.2.211:11435/cast_video/1c504eb5640b.mp4"

print("========================================")
print("Final Roku Playback Verification")
print("========================================")
print(f"Target: {ROKU_IP}")
print(f"Video:  {VIDEO_URL}")

# 1. Wake TV
print("\n1. Waking TV...")
requests.post(f"http://{ROKU_IP}:8060/keypress/PowerOn")
requests.post(f"http://{ROKU_IP}:8060/keypress/Home")
time.sleep(3)

# 2. Launch Play On Roku (15985) with exact params
print("\n2. Launching Play On Roku (15985)...")
endpoint = f"http://{ROKU_IP}:8060/launch/15985"
params = {
    "contentId": VIDEO_URL,
    "mediaType": "movie", 
    "u": VIDEO_URL
}

print(f"   URL: {endpoint}")
print(f"   Params: {params}")

try:
    resp = requests.post(endpoint, params=params, timeout=10)
    print(f"   Response: {resp.status_code}")
    
    if resp.status_code == 200:
        print("\n✅ Command sent successfully!")
        print("   Watch the TV - the video should start playing shortly.")
        
        # Monitor active app for 10 seconds
        print("\n3. Monitoring active app state...")
        for i in range(5):
            time.sleep(2)
            try:
                r = requests.get(f"http://{ROKU_IP}:8060/query/active-app", timeout=2)
                print(f"   [{i*2}s] Active App: {r.text.replace(chr(10), ' ').strip()[:100]}...")
            except:
                pass
    else:
        print(f"❌ Failed: {resp.text}")

except Exception as e:
    print(f"❌ Exception: {e}")

print("\n========================================")
print("Test Complete")
print("Did it play? (yes/no)")
print("========================================")
