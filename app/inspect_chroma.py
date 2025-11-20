# inspect_chroma.py
import os
import chromadb
# We still need the Embedding function defined, even if not used for listing
from langchain_community.embeddings import HuggingFaceEmbeddings 
from langchain_chroma import Chroma
import warnings

# Suppress LangChain deprecation warnings for cleaner output
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)


# --- Configuration ---
CHROMA_DIR = os.getenv("CHROMA_PERSIST_DIR", "/data/chroma_db")
EMB_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

def inspect_chroma():
    print(f"Inspecting Chroma DB at: {CHROMA_DIR}\n")
    
    try:
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        collections_list = client.list_collections()
        collection_names = [col.name for col in collections_list]

        if not collection_names:
            print("No collections found in ChromaDB.")
            return

        print(f"Successfully accessed ChromaDB. Found collections: {collection_names}\n")

        # Initialize Embeddings *once* (required for instantiating the Chroma wrapper later)
        embeddings = HuggingFaceEmbeddings(model_name=EMB_MODEL)

        for coll_name in collection_names:
            print(f"=== Collection: {coll_name} ===")
            vectordb = Chroma(
                persist_directory=CHROMA_DIR, 
                collection_name=coll_name,
                embedding_function=embeddings 
            )
            
            # Access the underlying collection object for count and peek
            collection = vectordb._collection
            count = collection.count()
            print(f"Total Documents: {count}\n")

            if count == 0:
                print("No documents in this collection.\n")
                continue

            # Peek at a few documents
            sample_count = min(count, 15)
            samples = collection.peek(sample_count)

            for i, doc in enumerate(samples["documents"]):
                metadata = samples["metadatas"][i]
                source = metadata.get("source", "unknown")
                path = metadata.get("path", "N/A")
                print(f"--- Document {i+1} ---")
                print(f"Source: {source}")
                print(f"Path: {path}")
                # Ensure the document content is accessible and truncated
                preview_text = doc[:300].strip() if doc else "[Empty Content]"
                print(f"Preview: {preview_text}...\n")

    except Exception as e:
        print(f"Critical Error inspecting ChromaDB: {e}")
        # Provide a more specific hint for direct client failure
        print("   HINT: Ensure the `chromadb` library is installed and the directory path is correct.")

if __name__ == "__main__":
    inspect_chroma()
