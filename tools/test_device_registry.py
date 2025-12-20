#!/usr/bin/env python3
"""
Test Home Assistant Device Registry API access
"""
import os
import requests
import json

# Load from .env
HA_URL = os.getenv("HA_URL")
HA_TOKEN = os.getenv("HA_TOKEN")

if not HA_URL or not HA_TOKEN:
    print("ERROR: HA_URL and HA_TOKEN must be set")
    exit(1)

headers = {"Authorization": f"Bearer {HA_TOKEN}"}

print("=" * 70)
print("Testing Home Assistant Device Registry API")
print("=" * 70)

# Test 1: Get entity state
print("\n1. Testing /api/states/media_player.28_tcl_roku_tv")
resp = requests.get(f"{HA_URL}/api/states/media_player.28_tcl_roku_tv", headers=headers, timeout=5)
print(f"   Status: {resp.status_code}")
if resp.status_code == 200:
    data = resp.json()
    print(f"   Entity ID: {data.get('entity_id')}")
    attrs = data.get('attributes', {})
    print(f"   Attributes keys: {list(attrs.keys())}")
    print(f"   device_id: {attrs.get('device_id', 'NOT FOUND')}")
else:
    print(f"   Error: {resp.text}")

# Test 2: Try device registry endpoint
print("\n2. Testing /api/config/device_registry/list")
resp = requests.get(f"{HA_URL}/api/config/device_registry/list", headers=headers, timeout=5)
print(f"   Status: {resp.status_code}")
if resp.status_code == 200:
    devices = resp.json()
    print(f"   Total devices: {len(devices)}")
    # Find Roku
    for device in devices:
        manufacturer = device.get("manufacturer", "").lower()
        model = device.get("model", "").lower()
        if "roku" in manufacturer or "tcl" in manufacturer or "roku" in model:
            print(f"\n   Found Roku Device:")
            print(f"     ID: {device.get('id')}")
            print(f"     Name: {device.get('name')}")
            print(f"     Manufacturer: {device.get('manufacturer')}")
            print(f"     Model: {device.get('model')}")
            print(f"     configuration_url: {device.get('configuration_url')}")
            print(f"     Identifiers: {device.get('identifiers')}")
            break
else:
    print(f"   Error: {resp.text}")
    print(f"   This endpoint might not be available or requires different permissions")

# Test 3: Alternative - try webhook/diagnostics
print("\n3. Testing alternative: /api/states (search all for device info)")
resp = requests.get(f"{HA_URL}/api/states", headers=headers, timeout=5)
if resp.status_code == 200:
    states = resp.json()
    print(f"   Total entities: {len(states)}")
    # Find Roku entities
    roku_entities = [s for s in states if '28_tcl_roku_tv' in s.get('entity_id', '')]
    print(f"   Roku-related entities: {len(roku_entities)}")
    for entity in roku_entities[:3]:  # Show first 3
        print(f"     - {entity.get('entity_id')}: {list(entity.get('attributes', {}).keys())}")

print("\n" + "=" * 70)
