
import asyncio
import logging
import os
import sys
import requests
from dotenv import load_dotenv

sys.path.append(os.getcwd())
logging.basicConfig(level=logging.INFO)
load_dotenv()

HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")
REMOTE_ID = "remote.28_tcl_roku_tv"

headers = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}

async def main():
    print(f"--- Live PowerOn Test for {REMOTE_ID} ---")
    
    # Try PowerOn
    print(">>> Sending Remote PowerOn...")
    url = f"{HA_URL}/api/services/remote/send_command"
    payload = {"entity_id": REMOTE_ID, "command": "PowerOn"}
    
    resp = requests.post(url, headers=headers, json=payload, timeout=10)
    print(f"Response: {resp.status_code} - {resp.text}")

if __name__ == "__main__":
    asyncio.run(main())
