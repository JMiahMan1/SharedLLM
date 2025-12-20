#!/usr/bin/env python3
"""
Test Roku ECP launch directly with the exact parameters
"""
import requests

ROKU_IP = "192.168.2.166"
VIDEO_URL = "http://192.168.2.211:11435/cast_video/1c504eb5640b.mp4"

print("Testing Roku ECP direct launch...")
print(f"Roku IP: {ROKU_IP}")
print(f"Video URL: {VIDEO_URL}")

# Test 1: Launch Roku Media Player with contentID
ecp_url = f"http://{ROKU_IP}:8060/launch/15985"
params = {"contentID": VIDEO_URL}

print(f"\nSending POST to: {ecp_url}")
print(f"Parameters: {params}")

response = requests.post(ecp_url, params=params, timeout=10)
print(f"\nResponse Status: {response.status_code}")
print(f"Response Headers: {dict(response.headers)}")
print(f"Response Body: {response.text[:500]}")

# Test 2: Check what's actually running on Roku
print("\n" + "="*70)
print("Checking Roku active app...")
app_resp = requests.get(f"http://{ROKU_IP}:8060/query/active-app", timeout=5)
print(f"Active App Response:\n{app_resp.text[:500]}")
