"""
Test MA (Music Assistant) Home Assistant integration services.
Used to debug/verify MA capabilities through HA proxy.

Usage:
  python scripts/test_ma_ha_proxy.py

Requires:
  - .env file with HA_TOKEN or resolves via identity service
  - MA integration configured in HA
"""

import asyncio
import json
import os
import sys

import httpx

# Resolve credentials from identity service
IDENTITY_URL = os.environ.get("IDENTITY_URL", "http://172.26.0.3:8001")
INTERNAL_SECRET = os.environ.get("INTERNAL_SECRET", "RAVEN_SECURE_2026")


async def resolve_credentials():
    """Get HA token from identity service."""
    async with httpx.AsyncClient() as client:
        headers = {"X-Internal-Secret": INTERNAL_SECRET}
        resp = await client.post(
            f"{IDENTITY_URL}/api/resolve",
            json={},
            headers=headers,
            timeout=10
        )
        if resp.status_code != 200:
            print(f"Failed to resolve credentials: {resp.status_code}")
            sys.exit(1)
        data = resp.json()
        return data.get("ha_token"), data


async def test_ma_get_queue(ha_token):
    """Test music_assistant.get_queue service."""
    print("\n=== music_assistant.get_queue ===")
    async with httpx.AsyncClient(verify=False) as client:
        headers = {"Authorization": f"Bearer {ha_token}"}

        # Get MA player entities
        resp = await client.get(
            "https://ha.sumemail.com/api/states?domain=media_player",
            headers=headers,
            timeout=10
        )
        players = resp.json()

        ma_players = []
        for p in players:
            app_id = p["attributes"].get("app_id", "")
            if "music_assistant" in str(app_id):
                ma_players.append(p["entity_id"])

        if not ma_players:
            print("No MA players found")
            return

        print(f"MA players: {ma_players}")

        for player_id in ma_players:
            resp = await client.post(
                "https://ha.sumemail.com/api/services/music_assistant/get_queue?return_response=1",
                headers=headers,
                json={"entity_id": player_id},
                timeout=10
            )
            print(f"\n{player_id}: {resp.status_code}")
            if resp.status_code == 200:
                print(json.dumps(resp.json(), indent=2)[:1000])


async def test_ma_player_states(ha_token):
    """Get detailed MA player states."""
    print("\n=== MA Player States ===")
    async with httpx.AsyncClient(verify=False) as client:
        headers = {"Authorization": f"Bearer {ha_token}"}

        resp = await client.get(
            "https://ha.sumemail.com/api/states?domain=media_player",
            headers=headers,
            timeout=10
        )
        players = resp.json()

        for p in players:
            app_id = p["attributes"].get("app_id", "")
            if "music_assistant" in str(app_id):
                print(f"\n{p['entity_id']}:")
                for k, v in p["attributes"].items():
                    if k in ("entity_id", "friendly_name", "supported_features", "icon"):
                        continue
                    sv = str(v)
                    if len(sv) > 200:
                        sv = sv[:200] + "..."
                    print(f"  {k}: {sv}")


async def test_ha_services(ha_token):
    """List HA services available."""
    print("\n=== Available HA Services ===")
    async with httpx.AsyncClient(verify=False) as client:
        headers = {"Authorization": f"Bearer {ha_token}"}

        resp = await client.get(
            "https://ha.sumemail.com/api/services",
            headers=headers,
            timeout=10
        )
        services = resp.json()

        for svc in services:
            domain = svc["domain"]
            if "media" in domain.lower() or "assist" in domain.lower() or "music_assistant" in domain.lower():
                print(f"\n{domain}:")
                for svc_name in svc["services"]:
                    print(f"  music_assistant.{svc_name}" if "music_assistant" in domain else f"  {domain}.{svc_name}")


async def main():
    ha_token, all_data = await resolve_credentials()
    if not ha_token:
        print("No HA token found")
        sys.exit(1)

    print(f"HA URL: {all_data.get('ha_url', 'N/A')}")
    print(f"MA URL: {all_data.get('mass_url', 'N/A')}")

    await test_ha_services(ha_token)
    await test_ma_player_states(ha_token)
    await test_ma_get_queue(ha_token)


if __name__ == "__main__":
    asyncio.run(main())
