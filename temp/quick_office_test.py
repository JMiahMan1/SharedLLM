#!/usr/bin/env python3
"""Quick test to verify Office TV entity resolution fix"""
import time, requests, os

API_URL = "http://192.168.2.211:11435"
HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
HA_TOKEN = os.getenv("HA_TOKEN", "")

API_HEADERS = {"X-RAG-User": "admin", "Content-Type": "application/json"}
HA_HEADERS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

OFFICE_TV_ENTITY = "media_player.office_tv_chrome_2"

def send_cmd(msg):
    print(f"\n[CMD] {msg}")
    resp = requests.post(f"{API_URL}/api/chat", json={"messages": [{"role": "user", "content": msg}]}, headers=API_HEADERS, timeout=60)
    print(f"[RESP] {resp.status_code}")
    return resp.status_code == 200

def get_state(entity_id):
    resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=HA_HEADERS, timeout=5)
    return resp.json().get("state") if resp.status_code == 200 else "error"

# Test commands
print("="*60)
print("OFFICE TV ENTITY RESOLUTION TEST")
print("="*60)

# Force OFF first
requests.post(f"{HA_URL}/api/services/media_player/turn_off", headers=HA_HEADERS, json={"entity_id": OFFICE_TV_ENTITY}, timeout=10)
time.sleep(3)

print(f"\nInitial state: {get_state(OFFICE_TV_ENTITY)}")

# Test 1: Play command
send_cmd("Play Brandon Lake on Office TV")
time.sleep(5)
state1 = get_state(OFFICE_TV_ENTITY)
print(f"After 'Play': {state1}")

#Test 2: Pause
send_cmd("Pause music on Office TV")
time.sleep(3)
state2 = get_state(OFFICE_TV_ENTITY)
print(f"After 'Pause': {state2}")

# Test 3: Resume  
send_cmd("Resume music on Office TV")
time.sleep(3)
state3 = get_state(OFFICE_TV_ENTITY)
print(f"After 'Resume': {state3}")

# Test 4: Stop
send_cmd("Stop music on Office TV")
time.sleep(3)
state4 = get_state(OFFICE_TV_ENTITY)
print(f"After 'Stop': {state4}")

print("\n" + "="*60)
print("TEST COMPLETE")
print("="*60)
