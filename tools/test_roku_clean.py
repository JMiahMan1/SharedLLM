#!/usr/bin/env python3
"""
Clean verification test using ONLY contentId and mediaType.
"""
import requests
import time
import sys

ROKU_IP = "192.168.2.166"
VIDEO_URL = "http://192.168.2.211:11435/cast_video/1c504eb5640b.mp4"

print("========================================")
print("Clean ECP Launch Test")
print("========================================")
print(f"Target: {ROKU_IP}")

# Launch Play On Roku (15985) with MINIMAL params
print("\nLaunching Play On Roku (15985)...")
endpoint = f"http://{ROKU_IP}:8060/launch/15985"
params = {
    "contentId": VIDEO_URL,
    "mediaType": "movie"
}
# NO 'u' parameter

print(f"   URL: {endpoint}")
print(f"   Params: {params}")

try:
    resp = requests.post(endpoint, params=params, timeout=10)
    print(f"   Response: {resp.status_code}")
    
    if resp.status_code == 200:
        print("\n✅ Command sent successfully!")
        print("   Watch the TV...")
        
        # Monitor active app for 10 seconds
        print("\nMonitoring active app state...")
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
