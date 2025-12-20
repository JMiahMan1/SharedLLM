#!/usr/bin/env python3
"""
Test Roku Playback using DIAL protocol (Discovery and Launch).
"""
import requests
import time
import urllib.parse

ROKU_IP = "192.168.2.166"
VIDEO_URL = "http://192.168.2.211:11435/cast_video/1c504eb5640b.mp4"

print("Testing DIAL Launch for Play On Roku...")
print(f"Target: {ROKU_IP}")

# DIAL endpoint usually at /dial/apps/<AppName>
dial_url = f"http://{ROKU_IP}:8060/launch/15985" 
# Wait, "launch" is ECP. "dial/apps" is DIAL.
# Roku maps DIAL to ECP internally often but standard DIAL is different.
# Let's try the strict PlayOnRoku parameters often used by casting apps.

# Attempt 1: The "standard" PlayOnRoku params (again, just to be sure)
params = {
    "contentId": VIDEO_URL,
    "mediaType": "movie",
    "videoFormat": "mp4"
}
print(f"Sending ECP launch with videoFormat... {params}")
# We already tried this in variants?
# In user snippet: "mediaType": "movie", "u": ...
# In variants: We tried contentFormat.

# Let's try one more specific combination found in other open source tools
# for PlayOnRoku (15985):
# ip:8060/launch/15985?contentID=<url>&contentFormat=mp4

final_url = f"http://{ROKU_IP}:8060/launch/15985"
payload = {
    "contentID": VIDEO_URL,
    "contentFormat": "mp4"
}

try:
    resp = requests.post(final_url, params=payload)
    print(f"Response: {resp.status_code}")
except Exception as e:
    print(f"Error: {e}")

# Attempt 2: Roku Media Player (2213) with "streamFormat"
# Some docs say RMP takes streamFormat
print("\nAttempting RMP with streamFormat...")
rmp_url = f"http://{ROKU_IP}:8060/launch/2213"
rmp_params = {
    "u": VIDEO_URL,
    "t": "v",
    "streamFormat": "mp4"
}
try:
    resp = requests.post(rmp_url, params=rmp_params)
    print(f"Response: {resp.status_code}")
except Exception as e:
    print(f"Error: {e}")
