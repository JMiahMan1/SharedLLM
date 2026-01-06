#!/usr/bin/env python3
"""
Test multiple ECP launch configurations to find one that works for video playback.
Roku is picky about parameter names (case-sensitive) and endpoints.
"""
import requests
import time
import urllib.parse
import sys

ROKU_IP = "192.168.2.166"
# Using the cached video we know exists
VIDEO_URL = "http://192.168.2.211:11435/cast_video/1c504eb5640b.mp4"

def test_variant(name, channel_id, endpoint, params):
    print(f"\n[{name}] Testing {endpoint}/{channel_id}...")
    url = f"http://{ROKU_IP}:8060/{endpoint}/{channel_id}"
    print(f"  URL: {url}")
    print(f"  Params: {params}")
    
    try:
        # Go home first to reset state
        requests.post(f"http://{ROKU_IP}:8060/keypress/Home")
        time.sleep(2)
        
        resp = requests.post(url, params=params, timeout=10)
        print(f"  Response: {resp.status_code}")
        if resp.status_code == 200:
            print("  ✅ Command accepted. Waiting 5s to check active app...")
            time.sleep(5)
            # Check what's running
            r_app = requests.get(f"http://{ROKU_IP}:8060/query/active-app")
            print(f"  Active App Data: {r_app.text.replace(chr(10), ' ')}")
            
            return True
    except Exception as e:
        print(f"  ❌ Error: {e}")
    return False

variants = [
    {
        "name": "RMP Launch (Legacy)",
        "channel_id": "2213",
        "endpoint": "launch",
        "params": {
            "t": "v",
            "u": VIDEO_URL,
            "videoName": "Test Stream",
            "videoFormat": "mp4"
        }
    },
    {
        "name": "RMP Launch (ContentID style)",
        "channel_id": "2213",
        "endpoint": "launch",
        "params": {
            "contentId": VIDEO_URL,
            "mediaType": "movie"
        }
    },
    {
        "name": "PlayOnRoku Launch (Standard)",
        "channel_id": "15985",
        "endpoint": "launch",
        "params": {
            "contentID": VIDEO_URL,
            "contentFormat": "mp4",
            "videoFormat": "mp4"
        }
    },
    {
        "name": "PlayOnRoku Input (If running)",
        "channel_id": "15985",
        "endpoint": "input",
        "params": {
            "t": "v",
            "u": VIDEO_URL,
            "videoName": "Stream",
            "videoFormat": "mp4"
        }
    }
]

print(f"Target Roku: {ROKU_IP}")
print(f"Video URL: {VIDEO_URL}")

for v in variants:
    print("-" * 50)
    test_variant(v["name"], v["channel_id"], v["endpoint"], v["params"])
    print("Does the TV show video? (Ctrl+C to stop if successful)")
    time.sleep(3)
