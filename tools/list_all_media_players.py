#!/usr/bin/env python3
import requests
import os
import json

HA_URL = os.getenv("HA_URL", "https://ha.sumemail.com")
HA_TOKEN = os.getenv("HA_TOKEN")

headers = {
    "Authorization": f"Bearer {HA_TOKEN}",
    "Content-Type": "application/json",
}

def list_players():
    response = requests.get(f"{HA_URL}/api/states", headers=headers)
    if response.status_code == 200:
        for s in response.json():
            if s["entity_id"].startswith("media_player."):
                print(f"{s['entity_id']}: {s.get('attributes', {}).get('friendly_name')} (State: {s['state']}) integrated with {s.get('attributes', {}).get('app_id', 'unknown')}")

if __name__ == "__main__":
    list_players()
