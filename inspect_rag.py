import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions
import os

CHROMA_DIR = "/data/chroma_db"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

def inspect():
    client = chromadb.PersistentClient(path=CHROMA_DIR, settings=Settings(anonymized_telemetry=False))
    embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
    
    collections = ["nextcloud_files", "ha_entities"]
    for name in collections:
        coll = client.get_or_create_collection(name=name, embedding_function=embedding_fn)
        results = coll.get(where={"user_id": "summers"}, include=["metadatas"])
        print(f"Collection: {name}")
        print(f"  Total Chunks for 'summers': {len(results['ids'])}")
        
        unique_paths = set()
        for m in results["metadatas"]:
            path = m.get("path") or m.get("friendly_name") or m.get("entity_id")
            if path:
                unique_paths.add(path)
        
        print(f"  Unique Documents for 'summers': {len(unique_paths)}")
        if len(unique_paths) > 0:
            print(f"  First 10 paths: {list(unique_paths)[:10]}")

if __name__ == "__main__":
    inspect()
