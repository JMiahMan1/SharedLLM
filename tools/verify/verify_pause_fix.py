#!/usr/bin/env python3
"""
Manual test to verify Pause command now uses remote Play button.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

import time

import requests

from app.users import get_user_creds

BASE_URL = "http://ai.local:11435"
ROKU_ENTITY = "media_player.roku_2n0062385487"

async def main():
    print("=" * 70)
    print("Testing Pause Fix for Roku Video")
    print("=" * 70)

    get_user_creds("admin")

    def chat(query):
        resp = requests.post(f"{BASE_URL}/api/chat", json={"query": query, "user": "admin"}, timeout=120)
        return resp.json().get('message', {}).get('content', 'N/A')[:80]

    def get_state(entity_id):
        resp = requests.get(f"{BASE_URL}/api/ha/state/{entity_id}", timeout=5)
        data = resp.json()
        return data.get("state"), data.get("attributes", {}).get("app_id", "N/A")

    # Step 1: Turn off to clear state
    print("\n[1] Turning off...")
    chat("Turn off Gracies TV")
    time.sleep(2)

    # Step 2: Start video
    print("\n[2] Starting VIDEO...")
    response = chat("Watch Tim Timmons on Gracies TV")
    print(f"  Response: {response}")
    time.sleep(15)
    state, app = get_state(ROKU_ENTITY)
    print(f"  State: {state}, App: {app}")

    # Step 3: Send PAUSE command (should now use remote Play button)
    print("\n[3] Sending PAUSE command...")
    response = chat("Pause")
    print(f"  Response: {response}")
    time.sleep(5)
    state, app = get_state(ROKU_ENTITY)
    print(f"  State: {state}, App: {app}")
    print("  Expected: State 'paused' or 'off', App should still be 'music_assistant'")

    # Step 4: Send RESUME command
    print("\n[4] Sending RESUME command...")
    response = chat("Resume")
    print(f"  Response: {response}")
    time.sleep(5)
    state, app = get_state(ROKU_ENTITY)
    print(f"  State: {state}, App: {app}")
    print("  Expected: State 'playing', App 'music_assistant'")

    # Step 5: Clean up
    print("\n[5] Stopping and turning off...")
    chat("Stop")
    time.sleep(2)
    chat("Turn off Gracies TV")

    print("\n" + "=" * 70)
    print("Test Complete!")
    print("\nNOTE: Check if Pause used remote.send_command in logs")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
