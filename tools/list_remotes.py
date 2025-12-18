
import asyncio
import sys
import os

# Ensure we can import app modules
sys.path.append(os.getcwd())

try:
    from app.settings import load_resources, GlobalResources
except ImportError:
    sys.path.append("/app")
    from app.settings import load_resources, GlobalResources

async def main():
    print("Loading resources...")
    await load_resources()
    
    if not GlobalResources.ha_collection:
        print("ERROR: HA Collection is None")
        return

    print("\n--- Listing All 'remote.*' Entities ---")
    res = GlobalResources.ha_collection._collection.get(include=["metadatas"])
    metas = res.get("metadatas", [])
    
    found = False
    for m in metas:
        eid = m.get("entity_id", "")
        if eid.startswith("remote."):
            print(f"Found: {eid} | Name: {m.get('friendly_name')} | Integration: {m.get('integration')}")
            found = True
            
    if not found:
        print("No 'remote.*' entities found.")

if __name__ == "__main__":
    asyncio.run(main())
