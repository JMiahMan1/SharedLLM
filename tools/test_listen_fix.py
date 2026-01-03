#!/usr/bin/env python3
"""Quick test for 'Listen to' intent fix"""
import requests

REMOTE_URL = "http://192.168.2.211:11435/api/chat"

print("Testing: 'Listen to Brandon Lake on Gracies TV'")
print("="*80)

response = requests.post(
    REMOTE_URL,
    json={"query": "Listen to Brandon Lake on Gracies TV"},
    timeout=120
)

if response.status_code == 200:
    data = response.json()
    content = data.get("message", {}).get("content", "")
    print(f"✓ Status: {response.status_code}")
    print(f"✓ Response: {content}")
else:
    print(f"✗ Failed: {response.status_code}")
