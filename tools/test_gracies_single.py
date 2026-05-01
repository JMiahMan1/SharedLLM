#!/usr/bin/env python3
"""Test single query to see entity resolution"""
import requests
import json

REMOTE_URL = "http://192.168.2.205:11435/api/chat"

query = "Watch funny cat videos on Gracies TV"

print(f"Testing query: '{query}'")
print("=" * 80)

response = requests.post(
    REMOTE_URL, 
    json={"query": query},
    timeout=120
)

print(f"Status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    content = data.get("message", {}).get("content", "")
    print(f"Response: {content}")
    print("\nFull response JSON:")
    print(json.dumps(data, indent=2))
else:
    print(f"Error: {response.text}")
