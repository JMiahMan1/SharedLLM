import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
import os

CHROMA_DIR = "/data/chroma_db"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

def fix_user_ids():
    client = chromadb.PersistentClient(path=CHROMA_DIR, settings=Settings(anonymized_telemetry=False))
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
    
    collections = ["nextcloud_files", "ha_entities"]
    for name in collections:
        coll = client.get_or_create_collection(name=name, embedding_function=embedding_fn)
        # Get everything
        results = coll.get(include=["metadatas"])
        if not results or not results["ids"]:
            continue
            
        print(f"Checking collection: {name}")
        ids_to_update = []
        new_metas = []
        
        for cid, meta in zip(results["ids"], results["metadatas"]):
            user_id = meta.get("user_id")
            if user_id and user_id != user_id.lower():
                print(f"  Fixing user_id: {user_id} -> {user_id.lower()} for {cid}")
                meta["user_id"] = user_id.lower()
                ids_to_update.append(cid)
                new_metas.append(meta)
        
        if ids_to_update:
            coll.update(ids=ids_to_update, metadatas=new_metas)
            print(f"  Updated {len(ids_to_update)} entries in {name}")
        else:
            print(f"  No case mismatches found in {name}")

if __name__ == "__main__":
    fix_user_ids()
