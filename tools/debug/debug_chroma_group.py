#!/usr/bin/env python3
"""
Debug script to inspect ChromaDB group membership for Office TV Chrome.
"""
import asyncio
import logging
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Bootstrap not needed for script
from app.settings import CHROMA_DIR as CHROMA_DB_PATH  # noqa: E402

# Setup logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("DebugGroup")

ENTITY_ID = "media_player.office_tv_chrome"

async def debug_group_lookup():
    print(f"\n[DEBUG] Inspecting Group for: {ENTITY_ID}")
    
    # 1. Initialize DB (Simulate startup or rely on existing?)
    # We need to manually load the collection if not running full app
    # But usually GlobalResources is populated by the app startup.
    # Here checking it might be empty if we don't init.
    
    # Assuming we can attach to running instance or just check the logic?
    # Actually, running this effectively requires the DB to be populated.
    # The ChromaDB is persistent? Yes.
    
    # from app.logic.rag.chroma import ChromaDBClient  <-- Invalid
    
    # Re-instantiate client to read existing DB
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)  # type: ignore[attr-defined]
    try:
        collection = client.get_collection("home_assistant")
    except Exception as e:
        print(f"❌ Collection 'home_assistant' not found: {e}")
        return
    
    # 1. Get Group ID
    print(f"Querying ID: {ENTITY_ID}")
    res = collection.get(ids=[ENTITY_ID], include=["metadatas"])
    
    if not res['metadatas']:
         print("❌ Entity not found in ChromaDB")
         return

    meta = res['metadatas'][0]
    group_id = meta.get("group_id")
    print(f"Found Metadata: {meta}")
    print(f"Group ID: {group_id}")
    
    if not group_id:
        print("❌ No Group ID assigned")
        return
        
    # 2. Get Group Members
    print(f"\nQuerying Members of Group: {group_id}")
    group_res = collection.get(where={"group_id": group_id}, include=["metadatas"])
    
    for i, m in enumerate(group_res['metadatas']):
        print(f"  [{i}] Entity: {m.get('entity_id')}")
        print(f"      Integration: {m.get('integration')}")
        print(f"      App ID: {m.get('app_id')}")
        print(f"      Friendly Name: {m.get('friendly_name')}")
        print(f"      -")

if __name__ == "__main__":
    asyncio.run(debug_group_lookup())
