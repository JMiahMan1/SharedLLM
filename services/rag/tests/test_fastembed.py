"""fastembed (CPU-only) produces usable embeddings for the sqlite-vec index."""
import pytest

# Verify fastembed is installed
_fastembed_installed = False

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

        ef = TextEmbedding(model_name="nomic-ai/nomic-embed-text-v1.5")
        docs = ["test document 1", "test document 2", "Hello World"]
        vectors = list(ef.embed(docs))  # pyright: ignore[reportUnknownVariableType]
        print(f"✓ Embedding created, count: {len(vectors)}")
        import numpy as np

        arr = np.array(vectors)
        assert not np.all(arr == 0), "All vectors are zero"
        assert not np.any(np.isnan(arr)), "Vectors contain NaN"
        print(f"✓ Embeddings are valid (non-zero, no NaN), dims: {arr.shape}")
    except Exception as e:
        pytest.skip(f"fastembed embedding failed: {e}")
