#!/usr/bin/env python3
"""Quick test for 'Listen to' intent fix"""
import os
import sys

import requests

LIVE_TV_NAME = os.environ.get("LIVE_TV_NAME") or sys.exit("Set LIVE_TV_NAME to the friendly name of a TV to test (e.g. \"Bedroom TV\").")

REMOTE_URL = "http://ai.local:11435/api/chat"

print(f"Testing: 'Listen to Brandon Lake on {LIVE_TV_NAME}'")
print("="*80)

response = requests.post(
    REMOTE_URL,
    json={"query": f"Listen to Brandon Lake on {LIVE_TV_NAME}"},
    timeout=120
)

if response.status_code == 200:
    data = response.json()
    content = data.get("message", {}).get("content", "")
    print(f"✓ Status: {response.status_code}")
    print(f"✓ Response: {content}")
else:
    print(f"✗ Failed: {response.status_code}")
