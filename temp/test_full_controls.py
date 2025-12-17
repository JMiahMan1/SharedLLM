#!/usr/bin/env python3
"""Complete transport control test"""
import requests, time

API_URL = "http://192.168.2.211:11435"
API_HEADERS = {"X-RAG-User": "admin", "Content-Type": "application/json"}

def send_cmd(msg):
    print(f"\n>>> {msg}")
    resp = requests.post(f"{API_URL}/api/chat", json={"messages": [{"role": "user", "content": msg}]}, headers=API_HEADERS, timeout=60)
    result = resp.json().get('message', {}).get('content', 'N/A')[:100]
    print(f"<<< {resp.status_code}: {result}")
    time.sleep(3)
    return resp.status_code == 200

print("="*60)
print("FULL TRANSPORT CONTROL TEST")
print("="*60)

# Music should already be playing from previous test
print("\n1. PAUSE")
send_cmd("Pause music on Office TV")

print("\n2. RESUME")
send_cmd("Resume music on Office TV")

print("\n3. VOLUME DOWN")
send_cmd("Turn down volume on Office TV")

print("\n4. VOLUME UP")
send_cmd("Turn up volume on Office TV")

print("\n5. STOP")
send_cmd("Stop music on Office TV")

print("\n6. POWER OFF")
send_cmd("Turn off Office TV")

print("\n" + "="*60)
print("TEST COMPLETE")
print("="*60)
