
import asyncio
import logging
import sys
import os
import chromadb
from app.settings import CHROMA_DIR

sys.path.append(os.getcwd())

async def main():
    print(f"Connecting to Chroma at: {CHROMA_DIR}")
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    collection = client.get_or_create_collection(name="home_assistant")
    
    target = "media_player.master_bedroom_tv"
    print(f"\n--- Metadata for {target} ---")
    
    results = collection.get(ids=[target], include=["metadatas"])
    if results and results["metadatas"] and results["metadatas"][0]:
        meta = results["metadatas"][0]
        # pprint(meta)
        print(f"Integration: {meta.get('integration')}")
        print(f"Group ID: {meta.get('group_id')}")
        print(f"Friendly Name: {meta.get('friendly_name')}")
        
        # Check for siblings in group
        gid = meta.get('group_id')
        if gid:
            print(f"\n--- Group Siblings ({gid}) ---")
            group_res = collection.get(where={"group_id": gid}, include=["metadatas"])
            for m in group_res["metadatas"]:
                print(f"ID: {m.get('entity_id')} | Integ: {m.get('integration')}")
    else:
        print("Entity not found in ChromaDB.")

if __name__ == "__main__":
    asyncio.run(main())
