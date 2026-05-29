# debug_db.py
import os
from langchain_chroma import Chroma  # pyright: ignore[reportMissingImports]
from langchain_community.embeddings import FastEmbedEmbeddings

# Point this to your actual DB folder defined in .env or settings
CHROMA_DIR = "/data/chroma_db" 

def inspect():
    if not os.path.exists(CHROMA_DIR):
        print(f"❌ CRITICAL: Directory {CHROMA_DIR} does not exist.")
        return

    print(f"🔍 Inspecting Database at: {CHROMA_DIR}")
    
    # Initialize Embedding function (required to load Chroma, even just to peek)
    embeddings = FastEmbedEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    
    # 1. Check Home Assistant Collection
    # Try common names
    for name in ["home_assistant", "ha_entities", "entities"]:
        try:
            db = Chroma(collection_name=name, embedding_function=embeddings, persist_directory=CHROMA_DIR)
            count = db._collection.count()
            print(f"   📂 Collection '{name}': {count} documents")
            
            if count > 0:
                # Peek at one to see if it looks right
                print(f"      Sample: {db._collection.peek(limit=1)['metadatas'][0]}")
        except Exception as e:
            print(f"   ⚠️ Could not load '{name}': {e}")

    # 2. Check Nextcloud Collection
    try:
        db = Chroma(collection_name="nextcloud_docs", embedding_function=embeddings, persist_directory=CHROMA_DIR)
        print(f"   📂 Collection 'nextcloud_docs': {db._collection.count()} documents")
    except:
        pass

if __name__ == "__main__":
    inspect()
