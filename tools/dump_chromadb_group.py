#!/usr/bin/env python3
"""
Dump ChromaDB entries for a specific device group to inspect metadata.
Usage: python3 dump_chromadb_group.py "Office TV"
"""
import sys
import os
sys.path.insert(0, '/workspace')

from chromadb import PersistentClient
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import Chroma

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 dump_chromadb_group.py <group_name>")
        print('Example: python3 dump_chromadb_group.py "Office TV"')
        sys.exit(1)
    
    group_name = sys.argv[1]
    
    # Initialize embedding model
    print(f"Loading embedding model...")
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

    # Load ChromaDB
    print(f"Loading ChromaDB...")
    chroma_client = PersistentClient(path="/data/chroma_db")
    collection = Chroma(
        client=chroma_client,
        collection_name="home_assistant",
        embedding_function=embeddings
    )

    # Query for the group
    print(f"\n=== Querying for '{group_name}' ===\n")
    results = collection.similarity_search_with_score(group_name, k=10)

    if results:
        for i, (doc, distance) in enumerate(results):
            metadata = doc.metadata
            
            print(f"\n--- Result {i+1} (Distance: {distance:.4f}) ---")
            print(f"  Entity ID:     {metadata.get('entity_id', 'N/A')}")
            print(f"  Friendly Name: {metadata.get('friendly_name', 'N/A')}")
            print(f"  Integration:   {metadata.get('integration', 'N/A')}")
            print(f"  Device Type:   {metadata.get('device_type', 'N/A')}")
            print(f"  Domain:        {metadata.get('domain', 'N/A')}")
            print(f"  All Keys:      {list(metadata.keys())}")
    else:
        print("No results found!")

if __name__ == "__main__":
    main()
