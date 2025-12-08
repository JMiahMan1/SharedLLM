
import sys
import os
import asyncio
import json

# Add app to path
sys.path.append("/app")

# Import necessary modules
try:
    from logic.discovery.device_grouper import group_entities
    from logic.media_ops import smart_resolve_entity, _route_by_intent
    from logic.pipeline import SILENT_SUCCESS_TOKEN
    from settings import GlobalResources
except ImportError as e:
    print(f"Import Error: {e}")
    sys.exit(1)

# Mock Data simulating what we think is broken
# We will also try to fetch real data from HA if possible, but let's test logic first.

def test_grouping_logic():
    print("\n--- 1. Testing Grouping Logic (In-Memory) ---")
    mock_entities = [
        {"entity_id": "media_player.office_tv", "friendly_name": "Office TV", "attributes": {"device_class": "tv"}, "state": "off"},
        {"entity_id": "media_player.office_tv_chrome", "friendly_name": "Office TV Chrome", "attributes": {"app_id": "CC1AD845"}, "state": "off"},
        {"entity_id": "remote.office_tv_remote", "friendly_name": "Office TV Remote", "attributes": {}, "state": "off"}
    ]
    
    groups = group_entities(mock_entities)
    print(f"Groups Formed: {len(groups)}")
    for gid, data in groups.items():
        print(f"Group: {gid} | Members: {len(data['members'])}")
        for m in data['members']:
            print(f"  - {m['entity_id']} (Integ: {m.get('integration')})")
            
        # Test Routing
        print("  > Testing 'turn_off' routing...")
        selected = _route_by_intent("turn_off", data['members'], False, False)
        print(f"  > Selected for Turn Off: {selected['entity_id'] if selected else 'None'}")

def check_file_versions():
    print("\n--- 2. Checking File Versions on Disk ---")
    files = [
        "/app/logic/refresh_devices.py",
        "/app/logic/discovery/integration_helper.py",
        "/app/logic/pipeline.py"
    ]
    for f in files:
        if os.path.exists(f):
            print(f"File: {f}")
            # Check for specific recent strings
            with open(f, 'r') as fh:
                content = fh.read()
                if "last_updated" in content: print("  - [OK] Contains 'last_updated'")
                else: print("  - [FAIL] Missing 'last_updated'")
                
                if "SILENT_SUCCESS_TOKEN" in content:
                    if '"Done."' in content: print("  - [OK] Silent Token is 'Done.'")
                    else: print("  - [FAIL] Silent Token is NOT 'Done.'")
        else:
            print(f"File: {f} NOT FOUND")

if __name__ == "__main__":
    check_file_versions()
    test_grouping_logic()
