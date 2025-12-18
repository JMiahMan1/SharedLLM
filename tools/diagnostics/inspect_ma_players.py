from app.settings import GlobalResources
import json

# Force init if needed (context dependent)
try:
    from app.settings import get_chromadb_client
    # GlobalResources.init() # Mock or rely on existing
except:
    pass

def check_devices():
    print("\n=== CHECKING SPECIFIC ENTITIES ===")
    target_ids = ["media_player.office_tv_chrome_2", "media_player.office_tv_chrome"]
    
    # We can use the internal HA collection or just print what we find
    # Let's search by ID
    
    for tid in target_ids:
        results = GlobalResources.ha_collection._collection.get(
            ids=[tid],
            include=["metadatas", "documents"]
        )
        
        metas = results.get("metadatas", [])
        if metas:
            m = metas[0]
            print(f"\nEntity: {tid}")
            print(f"  Integration: {m.get('integration')}")
            print(f"  Group Name:  {m.get('group_name')}")
            print(f"  Friendly Name: {m.get('friendly_name')}")
            print(f"  Attributes: {m.get('attributes')}")
        else:
            print(f"\nEntity: {tid} NOT FOUND in ChromaDB")

    print("\n=== LISTING ALL MUSIC ASSISTANT INTEGRATION DEVICES ===")
    results = GlobalResources.ha_collection._collection.get(
        where={"integration": "music_assistant"},
        include=["metadatas"]
    )
    
    metas = results.get("metadatas", [])
    print(f"Found {len(metas)} MA devices:")
    for m in metas:
        print(f"  - {m.get('entity_id')} | Name: {m.get('friendly_name')} | Group: {m.get('group_name')}")

if __name__ == "__main__":
    check_devices()
