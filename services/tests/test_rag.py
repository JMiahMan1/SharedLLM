import os
import pytest
from fastapi.testclient import TestClient

os.environ["INTERNAL_SECRET"] = "test-secret"
# Use a fast in-memory embedding model for tests
os.environ["EMBEDDING_MODEL"] = "sentence-transformers/all-MiniLM-L6-v2"
os.environ["CHROMA_PERSIST_DIR"] = "/tmp/chroma_test_db"

from rag.main import app

client = TestClient(app)

def test_missing_internal_secret():
    resp = client.post("/rag/search", json={"query": "hello", "user_id": "alice"})
    assert resp.status_code == 422

def test_ingest_and_search(mocker):
    """
    Mock the chroma client to prevent downloading heavy torch models during tests
    if they aren't available, but we can mock the actual chroma response.
    """
    mock_collection = mocker.Mock()
    # Mock search return
    mock_collection.query.return_value = {
        "documents": [["Test doc content"]],
        "metadatas": [[{"user_id": "alice", "source": "test"}]]
    }
    
    mock_get_collection = mocker.patch("rag.main.get_collection", return_value=mock_collection)
    
    # Test Ingest
    ingest_resp = client.post("/rag/ingest", 
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_id": "alice",
            "content": "Test doc content",
            "metadata": {"source": "test"}
        }
    )
    assert ingest_resp.status_code == 200
    assert ingest_resp.json()["status"] == "SUCCESS"
    mock_collection.add.assert_called_once()
    
    # Test Search
    search_resp = client.post("/rag/search", 
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "query": "Test doc",
            "user_id": "alice",
            "k": 1
        }
    )
    assert search_resp.status_code == 200
    results = search_resp.json()["results"]
    assert len(results) == 1
    assert results[0]["content"] == "Test doc content"
    assert results[0]["metadata"]["user_id"] == "alice"
