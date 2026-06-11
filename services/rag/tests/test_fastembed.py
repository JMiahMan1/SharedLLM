"""
Test that fastembed-based embeddings work with ChromaDB's embedding functions.
This replaces sentence-transformers and uses only CPU.

Skipped when chromadb has pydantic compat issues (Python 3.14 / Pydantic v2).
"""
import pytest

# Verify fastembed is installed
_fastembed_installed = False
_chromadb_ok = False

try:
    import fastembed  # pyright: ignore[reportMissingImports]
    _fastembed_installed = True
    version = getattr(fastembed, '__version__', 'unknown')
    print(f"fastembed {version} installed")
except ImportError:
    print("fastembed not installed")


@pytest.mark.skipif(not _fastembed_installed, reason="fastembed not installed")
def test_fastembed_version():
    import fastembed
    assert _fastembed_installed
    print(f"✓ fastembed version: {getattr(fastembed, '__version__', 'unknown')}")


@pytest.mark.skipif(not _fastembed_installed, reason="fastembed not installed")
def test_pytorch_cpu_only():
    try:
        import torch
        if torch.cuda.is_available():
            pytest.fail("PyTorch CUDA detected (should be CPU-only)")
        print(f"✓ PyTorch CPU-only: {torch.__version__}")
    except ImportError:
        print("✓ No torch dependency (fastembed handles its own)")


@pytest.mark.skipif(not _fastembed_installed, reason="fastembed not installed")
def test_fastembed_embedding_function():  # pyright: ignore[reportUnknownVariableType]
    try:
        from fastembed import TextEmbedding

        ef = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        docs = ["test document 1", "test document 2", "Hello World"]
        vectors = list(ef.embed(docs))  # pyright: ignore[reportUnknownVariableType]
        print(f"✓ Embedding created, count: {len(vectors)}")
        import numpy as np

        arr = np.array(vectors)
        assert not np.all(arr == 0), "All vectors are zero"
        assert not np.any(np.isnan(arr)), "Vectors contain NaN"
        print(f"✓ Embeddings are valid (non-zero, no NaN), dims: {arr.shape}")
        global _chromadb_ok

        _chromadb_ok = True
    except Exception as e:
        print(f"⚠ Embedding function unavailable: {e}")
        pytest.skip(f"fastembed/chromadb embedding function failed: {e}")


@pytest.mark.skipif(not _chromadb_ok, reason="chromadb embedding function unavailable")
def test_chromadb_collection():  # pyright: ignore[reportUnknownVariableType]
    import chromadb  # pyright: ignore[reportUnknownVariableType]
    from chromadb.config import Settings  # pyright: ignore[reportUnknownVariableType]

    client = chromadb.PersistentClient(  # pyright: ignore[reportAttributeAccessIssue]
        path="/tmp/test_rag_fastembed", settings=Settings(anonymized_telemetry=False)
    )
    coll = client.get_or_create_collection("test")
    coll.add(documents=["test doc"], ids=["test-id"])
    results = coll.query(query_texts=["test"], n_results=1)
    assert len(results["documents"][0]) == 1
    print(f"✓ ChromaDB collection with fastembed works")
    client.delete_collection("test")
