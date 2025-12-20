#!/usr/bin/env python3
"""
Test real Android Cast flow via API (Server-side execution)
This triggers the REAL flow: Search -> Download (on server) -> Cast
"""
import requests
import time
import os

API_URL = "http://192.168.2.211:11435/api/chat"
HA_TOKEN = os.getenv("HA_TOKEN")

# Send chat request
print("📱 Sending: 'Watch Brandon Lake music videos on Gracies TV'")
response = requests.post(
    API_URL,
    json={"query": "Watch Brandon Lake music videos on Gracies TV"},
    headers={"X-RAG-User": "jeremiah"},
    timeout=120
)

if response.status_code == 200:
    data = response.json()
    print(f"🤖 Response: {data.get('message', {}).get('content', data)}")
else:
    print(f"❌ Error: {response.status_code} - {response.text}")

# Give it time to process
print("\n⏱️ Waiting 15s for download + cast...")
time.sleep(15)

# Check TV state
print("\n📺 Checking TV state...")
tv_response = requests.get(
    f"https://ha.sumemail.com/api/states/media_player.28_tcl_roku_tv",
    headers={"Authorization": f"Bearer {HA_TOKEN}"},
    timeout=5
)

if tv_response.status_code == 200:
    state = tv_response.json()
    print(f"   State: {state.get('state')}")
    print(f"   App: {state.get('attributes', {}).get('app_name', 'N/A')}")
    print(f"   Media: {state.get('attributes', {}).get('media_title', 'N/A')}")
else:
    print(f"   ⚠️ Could not fetch state: {tv_response.status_code}")
