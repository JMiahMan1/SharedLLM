#!/usr/bin/env python3
"""
Test Roku Watch Intent complete flow:
1. Turn On
2. Watch video
3. Pause (via remote Play)
4. Resume (via remote Play)
5. Stop (should go to home screen)
6. Turn Off
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

BASE_URL = "http://192.168.2.205:11435"
ROKU_ENTITY = "media_player.roku_2n0062385487"
ROKU_REMOTE = "remote.28_tcl_roku_tv"

async def main():
    print("=" * 70)
    print("Roku Watch Intent - Full Flow Test")
    print("=" * 70)
    
    user_creds = get_user_creds("admin")
    
    def chat(query):
        resp = requests.post(f"{BASE_URL}/api/chat", json={"query": query, "user": "admin"}, timeout=120)
        return resp.json().get('message', {}).get('content', 'N/A')[:80]
    
    def get_state(entity_id):
        resp = requests.get(f"{BASE_URL}/api/ha/state/{entity_id}", timeout=5)
        data = resp.json()
        state = data.get("state")
        app = data.get("attributes", {}).get("app_id", "N/A")
        return state, app
    
    async def remote_cmd(command, desc):
        print(f"\n  [{desc}] Sending remote command: {command}")
        result = await execute_ha_service(
            "remote", "send_command", ROKU_REMOTE, user_creds, 
            {"command": command}, None
        )
        print(f"    Result: {result.get('status')}")
        return result.get('status') == 'SUCCESS'
    
    # Step 1: Turn On
    print("\n[STEP 1] Turning ON Roku...")
    response = chat("Turn on Gracies TV")
    print(f"  Response: {response}")
    time.sleep(3)
    state, app = get_state(ROKU_ENTITY)
    print(f"  ✓ State: {state}")
    
    # Step 2: Watch video
    print("\n[STEP 2] Starting VIDEO (Watch intent)...")
    response = chat("Watch Tim Timmons on Gracies TV")
    print(f"  Response: {response}")
    time.sleep(15)
    state, app = get_state(ROKU_ENTITY)
    print(f"  ✓ State: {state}, App: {app}")
    
    # Step 3: Pause via remote
    print("\n[STEP 3] PAUSE via remote Play button...")
    await remote_cmd("Play", "PAUSE")
    time.sleep(3)
    state, app = get_state(ROKU_ENTITY)
    print(f"  ✓ State: {state}, App: {app}")
    
    # Step 4: Resume via remote
    print("\n[STEP 4] RESUME via remote Play button...")
    await remote_cmd("Play", "RESUME")
    time.sleep(3)
    state, app = get_state(ROKU_ENTITY)
    print(f"  ✓ State: {state}, App: {app}")
    
    # Step 5: Stop (should go to home screen)
    print("\n[STEP 5] STOP (should exit to home screen)...")
    response = chat("Stop")
    print(f"  Response: {response}")
    time.sleep(3)
    state, app = get_state(ROKU_ENTITY)
    print(f"  ✓ State: {state}, App: {app}")
    print(f"  Expected: State should be 'idle' or 'standby', App should NOT be media_assistant")
    
    # Step 6: Turn Off
    print("\n[STEP 6] Turning OFF Roku...")
    response = chat("Turn off Gracies TV")
    print(f"  Response: {response}")
    time.sleep(3)
    state, app = get_state(ROKU_ENTITY)
    print(f"  ✓ State: {state}")
    
    print("\n" + "=" * 70)
    print("Test Complete!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
