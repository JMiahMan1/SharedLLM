#!/usr/bin/env python3
"""
Test sending multiple HOME commands to Android TV
"""
import sys
sys.path.insert(0, '/home/jeremiah/Summers Drive/Code/SharedLLM')

import asyncio
from app.domains.shared import execute_ha_service
from app.users import get_user_creds

BASE_URL = "http://ai.local:11435"
ANDROID_TV_REMOTE = "remote.office_tv_remote"

async def main():
    user_creds = get_user_creds("admin")
    
    print("=" * 70)
    print("Testing Multiple HOME Commands for Android TV")
    print("=" * 70)
    
    # Send multiple HOME presses
    for i in range(3):
        print(f"\n[{i+1}] Sending HOME button...")
        result = await execute_ha_service(
            "remote",
            "send_command",
            ANDROID_TV_REMOTE,
            {k: v for k, v in user_creds.items() if v is not None},
            {"command": "HOME"},
            None
        )
        print(f"  Result: {result.get('status')}")
        await asyncio.sleep(2)
    
    print("\n" + "=" * 70)
    print("Check if TV went to home screen")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(main())
