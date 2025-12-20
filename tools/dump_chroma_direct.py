
import chromadb
import os
import sys

# Standard path from settings or env
# CHROMA_PERSIST_DIR in .env might be different if running locally vs docker
# I will try the local path first, assuming .env is loaded or I hardcode what I see.

# Prioritize the Environment Variable if set
CHROMA_ENV_PATH = os.getenv("CHROMA_PERSIST_DIR")

POSSIBLE_PATHS = [
    CHROMA_ENV_PATH,  # Check env var first
    "/data/chroma_db", # Check absolute Docker path next
    "data/chroma_db",  # Check local repo path last
    "./chroma_db",
    "chroma_db",
]
# Filter out None
POSSIBLE_PATHS = [p for p in POSSIBLE_PATHS if p]

def main():
    print("--- Direct Chroma Dump ---")
    
    db_path = None
    for p in POSSIBLE_PATHS:
        if os.path.exists(p):
            db_path = p
            break
            
    if not db_path:
        print(f"Could not find Chroma Path. checked: {POSSIBLE_PATHS}")
        # Try to read from .env manually if needed, but let's see.
        return

    print(f"Opening DB at {db_path}")
    client = chromadb.PersistentClient(path=db_path)
    
    try:
        coll = client.get_collection("home_assistant")
        print(f"Collection 'home_assistant' found. Count: {coll.count()}")
        
        # Get all ids and metadatas
        data = coll.get()
        metas = data['metadatas']
        ids = data['ids']
        
        found = False
        for i, m in zip(ids, metas):
            eid = m.get('entity_id', 'unknown')
            if eid.startswith("remote."):
                 print(f"Found Remote: {eid} | Name: {m.get('friendly_name')}")
                 found = True
        
        if not found:
            print("No remote.* entities in DB.")

    except Exception as e:
        print(f"Error reading collection: {e}")

if __name__ == "__main__":
    main()
