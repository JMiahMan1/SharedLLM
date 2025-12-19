#!/usr/bin/env python3
import requests
import json

# Get HA token
with open('/workspace/.env') as f:
    token = f.read().split('HA_TOKEN=')[1].split()[0]

headers = {'Authorization': f'Bearer {token}'}
r = requests.get('https://ha.sumemail.com/api/states', headers=headers)

office_devices = [d for d in r.json() if 'office' in d.get('attributes', {}).get('friendly_name', '').lower()]

print(f"Found {len(office_devices)} Office devices:\n")
for d in office_devices:
    attrs = d.get('attributes', {})
    print(f"\nEntity ID: {d['entity_id']}")
    print(f"Friendly Name: {attrs.get('friendly_name')}")
    print(f"State: {d['state']}")
    print(f"Device Class: {attrs.get('device_class', 'N/A')}")
    print(f"Domain: {d['entity_id'].split('.')[0]}")

