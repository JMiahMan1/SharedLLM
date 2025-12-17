#!/usr/bin/env python3
"""Quick test - Play Music to verify UnboundLocalError fix"""
import requests, time

API_URL = "http://192.168.2.211:11435"
API_HEADERS = {"X-RAG-User": "admin", "Content-Type": "application/json"}

print("Testing: Play Brandon Lake on Office TV")
resp = requests.post(
    f"{API_URL}/api/chat",
    json={"messages": [{"role": "user", "content": "Play Brandon Lake on Office TV"}]},
    headers=API_HEADERS,
    timeout=60
)
print(f"API Response: {resp.status_code}")
print(f"Response Text: {resp.json().get('message', {}).get('content', 'N/A')[:200]}")
