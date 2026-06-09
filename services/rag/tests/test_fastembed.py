"""
Test that fastembed-based embeddings work with ChromaDB's embedding functions.
This replaces sentence-transformers and uses only CPU.
"""
import sys

# Verify fastembed is installed and CPU-only
try:
    import fastembed  # pyright: ignore[reportMissingImports]
    print(f"✓ fastembed {fastembed.__version__} installed")
except ImportError:
    print("✗ fastembed not installed")
    sys.exit(1)

# Verify no torch CUDA deps pulled in
try:
    import torch
    if torch.cuda.is_available():
        print("✗ PyTorch CUDA detected (should be CPU-only)")
        sys.exit(1)
    print(f"✓ PyTorch CPU-only: {torch.__version__}")
except ImportError:
    print("✓ No torch dependency (fastembed handles its own)")

# Test embedding function creation (this is what RAG service does)
from chromadb.utils.embedding_functions import FastembedEmbeddingFunction  # pyright: ignore[reportAttributeAccessIssue]

try:
    ef = FastembedEmbeddingFunction(model_name="BAAI/bge-small-en-v1.5")
    docs = ["test document 1", "test document 2", "Hello World"]
    vectors = ef(docs)
    print(f"✓ Embedding created, shape: {vectors.shape}")
    print(f"  Model: BAAI/bge-small-en-v1.5")
    print(f"  Dimensions: {vectors.shape[1]}")
    print(f"  Sample vector[0][:5]: {vectors[0][:5].tolist()}")
    
    # Verify vectors are reasonable (not all zeros/nans)
    import numpy as np
    assert not np.all(vectors == 0), "All vectors are zero"
    assert not np.any(np.isnan(vectors)), "Vectors contain NaN"
    print("✓ Embeddings are valid (non-zero, no NaN)")
except Exception as e:
    print(f"✗ Embedding test failed: {e}")
    sys.exit(1)

# Test ChromaDB collection creation with fastembed
try:
    import chromadb
    from chromadb.config import Settings
    
    client = chromadb.PersistentClient(path="/tmp/test_rag_fastembed", settings=Settings(anonymized_telemetry=False))  # pyright: ignore[reportAttributeAccessIssue]
    coll = client.get_or_create_collection("test", embedding_function=ef)
    coll.add(documents=["test doc"], ids=["test-id"])
    results = coll.query(query_texts=["test"], n_results=1)
    assert len(results["documents"][0]) == 1
    print(f"✓ ChromaDB collection with fastembed works (collection: test)")
    client.delete_collection("test")
except Exception as e:
    print(f"✗ ChromaDB test failed: {e}")
    sys.exit(1)

print("\n✓ All tests passed — fastembed replacement is functional")
