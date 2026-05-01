#!/usr/bin/env python3
"""
Simple Roku Test - Just send chat commands and verify
NO power logic in test - the app should handle it
"""
import requests
import os
import time

API_URL = "http://ai.local:11435/api/chat"

print("=" * 70)
print("SIMPLE ROKU TEST - App Should Handle Power")
print("=" * 70)

def send_chat(message):
    print(f"\n📱 USER: {message}")
    try:
        resp = requests.post(API_URL, json={"query": message}, timeout=90)
        if resp.status_code == 200:
            data = resp.json()
            reply = data.get("message", {}).get("content") or data.get("response", "No response")
            print(f"🤖 ASSISTANT: {reply}")
            return reply
        else:
            print(f"❌ ERROR: {resp.status_code}")
            return None
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        return None

# Test 1: Simple playback command
result = send_chat("Play Big Buck Bunny video on Gracies TV")

# Give it time to download/cast
print("\n⏳ Waiting 15s for playback to start...")
time.sleep(15)

print("\n" + "=" * 70)
print("🔍 PLEASE VERIFY:")
print("   1. Is the TV display ON?")
print("   2. Is video playing or attempting to play?")
print("=" * 70)

# Turn off TV at end
print("\n🔴 Turning off TV...")
send_chat("Turn off Gracies TV")
time.sleep(2)
