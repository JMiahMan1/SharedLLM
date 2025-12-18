import asyncio
import json
import os
import sys

# Ensure we can import app modules
sys.path.append(os.getcwd())

try:
    from app.settings import load_resources, GlobalResources
except ImportError:
    # If running inside docker container where pwd might differ
    sys.path.append("/app")
    from app.settings import load_resources, GlobalResources

async def main():
    print("Initializing resources...")
    await load_resources()

    query = "office"
    if len(sys.argv) > 1:
        query = sys.argv[1]
    
    print(f"\n--- Searching for devices matching: '{query}' ---")
    
    if not GlobalResources.ha_collection:
        print("ERROR: HA Collection is None")
        return

    # Get all and filter in python to be safe with chroma query syntax variations
    res = GlobalResources.ha_collection._collection.get(include=["metadatas"])
    metas = res.get("metadatas", [])
    
    found = []
    for m in metas:
        s_id = m.get("entity_id", "")
        s_name = m.get("friendly_name", "")
        if query.lower() in s_id.lower() or query.lower() in s_name.lower():
            found.append(m)
            
    print(f"Found {len(found)} devices:")
    for m in found:
        print("\n---------------------------------------------------")
        print(f"ID: {m.get('entity_id')}")
        print(f"Name: {m.get('friendly_name')}")
        print(f"Group: {m.get('group_name')}")
        print(f"Integration: {m.get('integration')}")
        attrs = m.get('attributes', "")
        print(f"MA Attributes: 'mass_player' in attrs? {'mass_player' in str(attrs)}")
        print(f"Full Attrs: {attrs}")

if __name__ == "__main__":
    asyncio.run(main())
