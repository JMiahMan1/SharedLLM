
import asyncio
import logging
import sys
import os
import aiohttp
from dotenv import load_dotenv

sys.path.append(os.getcwd())
load_dotenv()

from app.settings import HA_URL

HA_TOKEN = os.getenv("HA_TOKEN")
HEADER = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

async def main():
    url = f"{HA_URL}/api/states"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=HEADER) as resp:
            if resp.status == 200:
                states = await resp.json()
                print("\n--- Available Media Players ---")
                for s in states:
                    eid = s['entity_id']
                    if "media_player" in eid:
                         friendly = s.get("attributes", {}).get("friendly_name")
                         print(f"ID: {eid} | Name: {friendly} | State: {s['state']} | Attrs: {s.get('attributes', {}).keys()}")
            else:
                print(f"Error: {resp.status}")

if __name__ == "__main__":
    asyncio.run(main())
