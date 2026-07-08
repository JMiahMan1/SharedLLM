#!/usr/bin/env python3
import logging
import os
import sys

import requests
from dotenv import load_dotenv

# Setup minimal logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger("inspect_players")

load_dotenv()

# Env setup (mimic app settings without importing whole app if possible, or minimal import)
_ha_url_from_app: str | None = None
try:
    from app.settings import HA_URL as _ha_url_from_app
except ImportError:
    pass

HA_URL: str = _ha_url_from_app or os.getenv("HA_URL", "")  # type: ignore[assignment]
HA_TOKEN: str = os.getenv("HA_ENV_TOKEN", "") or os.getenv("HA_TOKEN", "")  # type: ignore[assignment]

def get_headers():
    if not HA_TOKEN:
        log.error("HA_TOKEN not found. Set HA_TOKEN env var or run in app environment.")
        sys.exit(1)
    return {
        "Authorization": f"Bearer {HA_TOKEN}",
        "Content-Type": "application/json",
    }

def fetch_media_players():
    if not HA_URL:
        log.error("HA_URL not found.")
        sys.exit(1)

    url = f"{HA_URL.rstrip('/')}/api/states"
    try:
        resp = requests.get(url, headers=get_headers(), timeout=5)
        resp.raise_for_status()
        states = resp.json()

        players = [s for s in states if s['entity_id'].startswith("media_player.")]
        return players
    except Exception as e:
        log.error(f"Failed to fetch states from HA: {e}")
        return []

def print_player_details(player):
    attrs = player.get("attributes", {})
    eid = player['entity_id']
    state = player['state']
    fname = attrs.get("friendly_name", eid)

    print(f"\n--- {fname} ({eid}) ---")
    print(f"  State: {state.upper()}")

    # relevant attributes
    keys = ["app_name", "app_id", "volume_level", "is_volume_muted", "source", "source_list", "media_title", "media_artist", "mass_player_type", "active_queue"]

    for k in keys:
        if k in attrs:
            val = attrs[k]
            print(f"  {k}: {val}")

    # Check for Roku specific
    if "roku" in eid or "roku" in fname.lower():
        print("  [ROKU DETECTED]")

    # Check for MA specific
    if attrs.get("mass_player_type") or "music_assistant" in eid:
        print("  [MUSIC ASSISTANT DETECTED]")

def main():
    print(f"Inspecting Media Players on {HA_URL}...")
    players = fetch_media_players()
    print(f"Found {len(players)} media_player entities.")

    for p in players:
        print_player_details(p)

if __name__ == "__main__":
    main()
