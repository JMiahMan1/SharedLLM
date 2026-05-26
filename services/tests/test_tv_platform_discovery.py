#!/usr/bin/env python3
"""Discover TV platforms from Home Assistant and classify by brand."""
import asyncio
import httpx
import os

HA_TOKEN = os.environ.get('HA_TOKEN', '')
HA_URL = os.environ.get('HA_URL', 'http://localhost:8123')

async def main():
    print(f"HA_URL: {HA_URL}")
    print(f"HA_TOKEN: {HA_TOKEN[:20]}..." if HA_TOKEN else "HA_TOKEN: (empty)")
    
    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        resp = await client.get(
            f'{HA_URL}/api/states',
            headers={'Authorization': f'Bearer {HA_TOKEN}', 'Content-Type': 'application/json'}
        )
        if resp.status_code != 200:
            print(f"Failed: {resp.status_code} {resp.text[:200]}")
            return
        
        states = resp.json()
        tv_entities = [s for s in states if s['entity_id'].startswith('media_player.')]
        
        platforms = {"roku": [], "webos": [], "samsung": [], "android_tv": [], "chromecast": [], "unknown": []}
        
        for s in tv_entities:
            eid = s['entity_id']
            name = s.get('attributes', {}).get('friendly_name', '')
            app_id = s.get('attributes', {}).get('app_id', '')
            source_list = s.get('attributes', {}).get('source_list', [])
            state = s.get('state', '')
            
            platform = "unknown"
            eid_lower = eid.lower()
            app_lower = app_id.lower()
            
            if "roku" in eid_lower or "tcl" in eid_lower or "sharp" in eid_lower:
                platform = "roku"
            elif "webos" in eid_lower or "lg_" in eid_lower or "lg.webos" in app_lower:
                platform = "webos"
            elif "samsung" in eid_lower or "samsungtv" in eid_lower:
                platform = "samsung"
            elif "chrome" in eid_lower or "_cast" in eid_lower:
                platform = "chromecast"
            elif "com.google.android" in app_lower or "com.google.tv" in app_lower:
                platform = "android_tv"
            
            platforms[platform].append({
                'entity_id': eid,
                'name': name,
                'state': state,
                'app_id': app_id,
                'sources': source_list[:3] if source_list else []
            })
        
        for plat, devices in platforms.items():
            if devices:
                print(f"\n=== {plat.upper()} ({len(devices)} devices) ===")
                for d in devices:
                    print(f"  {d['entity_id']}")
                    print(f"    Name: {d['name']} | State: {d['state']}")
                    if d['app_id']:
                        print(f"    App ID: {d['app_id']}")
                    if d['sources']:
                        print(f"    Sources: {d['sources']}")

if __name__ == '__main__':
    asyncio.run(main())
