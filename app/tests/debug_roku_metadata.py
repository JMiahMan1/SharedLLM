
import asyncio
import logging
import sys
import os
from pprint import pprint

# Add app to path
sys.path.append(os.getcwd())

from app.settings import GlobalResources

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("RokuDebug")

async def main():
    print("\n--- Initializing Resources ---")
    # Initialize ChromaDB Directly
    import chromadb
    from app.settings import CHROMA_DIR
    
    print(f"Connecting to Chroma at: {CHROMA_DIR}")
    GlobalResources.chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    GlobalResources.ha_collection = GlobalResources.chroma_client.get_or_create_collection(name="home_assistant")
    
    # 1. Inspect Office Roku Metadata
    # Assuming 'media_player.office_roku' is the target, verify this exists first
    entities_to_check = ["media_player.office_roku", "media_player.roku_ultra", "media_player.living_room_tv"]
    
    print("\n--- Checking ChromaDB Metadata ---")
    if GlobalResources.ha_collection:
        # Get all media_players with roku in name if specific ones fail
        all_roku_docs = GlobalResources.ha_collection.get(
            where={"$and": [
                {"domain": "media_player"},
                {"friendly_name": {"$contains": "Roku"}}
            ]},
            include=["metadatas"]
        )
        
        if all_roku_docs and all_roku_docs.get("ids"):
             print(f"Found {len(all_roku_docs['ids'])} Roku-like devices:")
             for i, eid in enumerate(all_roku_docs["ids"]):
                 meta = all_roku_docs["metadatas"][i]
                 print(f"\n[Entity]: {eid}")
                 print(f"[Integration Field]: {meta.get('integration', 'MISSING')}")
                 print("-" * 20)
                 pprint(meta)
        else:
             print("No devices found matching 'Roku' in friendly_name.")
             
             # Fallback: Check strictly by known ID if user provided one
             specific_docs = GlobalResources.ha_collection.get(
                 ids=entities_to_check,
                 include=["metadatas"]
             )
             if specific_docs and specific_docs.get("ids"):
                  print("\nChecked specific IDs:")
                  for i, eid in enumerate(specific_docs["ids"]):
                       meta = specific_docs["metadatas"][i]
                       print(f"[Entity]: {eid}")
                       pprint(meta)

if __name__ == "__main__":
    asyncio.run(main())
