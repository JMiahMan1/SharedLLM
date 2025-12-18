
import chromadb
import os
import sys

# Standard path from settings or env
# CHROMA_PERSIST_DIR in .env might be different if running locally vs docker
# I will try the local path first, assuming .env is loaded or I hardcode what I see.

CHROMA_DB_PATH = "/home/jeremiah/Summers Drive/Code/SharedLLM/chroma_db" # Guessing based on workspace
# Actually, the user's workspace is /home/jeremiah/Summers Drive/Code/SharedLLM
# The container maps /data/chroma_db.
# I need to find where the DB is ON DISK.

# Let's check common locations first
POSSIBLE_PATHS = [
    "./chroma_db",
    "chroma_db",
    "data/chroma_db",
    "/data/chroma_db"
]

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
