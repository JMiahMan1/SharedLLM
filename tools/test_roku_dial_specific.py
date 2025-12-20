#!/usr/bin/env python3
"""
Test Roku Playback using DIAL protocol specific for Play On Roku.
Ref: https://github.com/dankrause/roku-cast/blob/master/roku.py
"""
import requests
import time

ROKU_IP = "192.168.2.166"
VIDEO_URL = "http://192.168.2.211:11435/cast_video/1c504eb5640b.mp4"

print("========================================")
print("DIAL Protocol Test for Play On Roku")
print("========================================")
print(f"Target: {ROKU_IP}")

# DIAL endpoint is strictly /dial/apps/<AppName>
# For Play On Roku, it's typically "PlayOnRoku" (case sensitive sometimes)
# Check installed apps to be sure of the name?
# query/apps gives "Play On Roku" (with spaces). DIAL usually removes spaces or uses ID.
# Let's try "PlayOnRoku" first.

dial_base = f"http://{ROKU_IP}:8060/dial/apps"
app_name = "PlayOnRoku" 
url = f"{dial_base}/{app_name}"

params = {
    "streamFormat": "mp4",
    "url": VIDEO_URL
}

print(f"\n1. POST to {url}")
print(f"   Params (Body): {params}")

try:
    # DIAL uses POST with params in body (x-www-form-urlencoded)
    resp = requests.post(url, data=params, timeout=10)
    print(f"   Response: {resp.status_code}")
    print(f"   Headers: {resp.headers}")
    print(f"   Body: {resp.text}")

    if resp.status_code == 201 or resp.status_code == 200:
         print("\n✅ DIAL Launch Accepted!")
    elif resp.status_code == 404:
        print("\n❌ 404 Not Found - trying 'Play On Roku' (with spaces)...")
        url_spaces = f"{dial_base}/Play On Roku"
        resp2 = requests.post(url_spaces, data=params, timeout=10)
        print(f"   Response 2: {resp2.status_code}")
        
        if resp2.status_code == 404:
             print("❌ 404 again. Trying via ECP launch with input params...")
             # Some docs say ECP is used after DIAL discovery.
    
except Exception as e:
    print(f"❌ Error: {e}")

print("\n2. Checking Active App (Wait 5s)...")
time.sleep(5)
try:
    r = requests.get(f"http://{ROKU_IP}:8060/query/active-app")
    print(f"   Active App: {r.text.replace(chr(10), ' ').strip()[:100]}...")
except:
    pass
