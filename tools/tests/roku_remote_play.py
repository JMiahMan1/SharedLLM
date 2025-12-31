#!/usr/bin/env python3
"""
Test Roku remote Play command using internal app infrastructure.
"""
import asyncio
import sys
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from app.domains.shared import execute_ha_service
from app.users import get_user_creds
import requests
import time

BASE_URL = "http://192.168.2.211:11435"

async def main():
    print("=" * 60)
    print("Roku Remote Play Command Test (Internal API)")
    print("=" * 60)
    
    # Get credentials
    user_creds = get_user_creds("admin")
    
    def chat(query):
        resp = requests.post(f"{BASE_URL}/api/chat", json={"query": query, "user": "admin"}, timeout=120)
        return resp.json().get('message', {}).get('content', 'N/A')[:100]
    
    def get_state(entity_id):
        resp = requests.get(f"{BASE_URL}/api/ha/state/{entity_id}", timeout=5)
        data = resp.json()
        return data.get("state"), data.get("attributes", {}).get("app_id", "N/A")
    
    # Step 1: Turn off
    print("\n[1] Turning off...")
    chat("Turn off Gracies TV")
    time.sleep(3)
    
    # Step 2: Start video
    print("\n[2] Starting video...")
    response = chat("Watch Tim Timmons on Gracies TV")
    print(f"  Response: {response}")
    time.sleep(15)
    
    # Step 3: Check state
    print("\n[3] Initial state...")
    state, app = get_state("media_player.roku_2n0062385487")
    print(f"  State: {state}, App: {app}")
    
    # Step 4: Send PAUSE
    print("\n[4] Sending PAUSE (remote Play command)...")
    result = await execute_ha_service(
        "remote",
        "send_command",
        "remote.28_tcl_roku_tv",
        user_creds,
        {"command": "Play"},
        None
    )
    print(f"  Result: {result.get('status')}")
    time.sleep(5)
    
    # Step 5: Check state after pause
    print("\n[5] State after pause...")
    state, app = get_state("media_player.roku_2n0062385487")
    print(f"  State: {state}, App: {app}")
    
    # Step 6: Send RESUME
    print("\n[6] Sending RESUME (remote Play command)...")
    result = await execute_ha_service(
        "remote",
        "send_command",
        "remote.28_tcl_roku_tv",
        user_creds,
        {"command": "Play"},
        None
    )
    print(f"  Result: {result.get('status')}")
    time.sleep(5)
    
    # Step 7: Final state
    print("\n[7] State after resume...")
    state, app = get_state("media_player.roku_2n0062385487")
    print(f"  State: {state}, App: {app}")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
