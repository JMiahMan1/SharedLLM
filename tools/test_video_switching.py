#!/usr/bin/env python3
"""
End-to-end test: Big Buck Bunny → Fireplace Video
Tests the full application flow through the chat API
"""
import requests
import time
import os

API_URL = "http://ai.local:11435/api/chat"

def send_chat(message):
    """Send a chat message and return the response"""
    print(f"\n{'='*60}")
    print(f"USER: {message}")
    print('='*60)
    
    response = requests.post(
        API_URL,
        json={"query": message},
        timeout=30
    )
    
    if response.status_code == 200:
        data = response.json()
        assistant_response = data.get("response", "No response")
        print(f"ASSISTANT: {assistant_response}")
        return True
    else:
        print(f"ERROR: {response.status_code} - {response.text}")
        return False

print("="*60)
print("END-TO-END CASTING TEST")
print("="*60)

# Test 1: Play Big Buck Bunny
print("\n[TEST 1] Playing Big Buck Bunny...")
if send_chat("Play Big Buck Bunny video on the Office TV"):
    print("✅ Big Buck Bunny command sent")
    time.sleep(3)
else:
    print("❌ Big Buck Bunny failed")
    exit(1)

# Wait for it to start
print("\n⏳ Waiting 5 seconds for Big Buck Bunny to start...")
time.sleep(5)

# Test 2: Switch to Fireplace
print("\n[TEST 2] Switching to Fireplace...")
if send_chat("Watch a fireplace video on the Office TV"):
    print("✅ Fireplace video command sent")
    time.sleep(3)
else:
    print("❌ Fireplace failed")
    exit(1)

print("\n" + "="*60)
print("TEST COMPLETE")
print("="*60)
print("\nCheck your Office TV to verify:")
print("1. Big Buck Bunny played first")
print("2. Then switched to Fireplace video")
