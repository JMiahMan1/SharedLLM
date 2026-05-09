import pytest
from fastapi.testclient import TestClient
import os

@pytest.fixture(name="client")
def client_fixture(monkeypatch):
    import sys
    from unittest.mock import MagicMock
    
    # Mock chromadb and embedding_functions before they are imported by main
    mock_chroma = MagicMock()
    mock_ef = MagicMock()
    sys.modules["chromadb"] = mock_chroma
    sys.modules["chromadb.config"] = MagicMock()
    sys.modules["chromadb.utils"] = MagicMock()
    sys.modules["chromadb.utils.embedding_functions"] = mock_ef
    
    # Mock the collection
    mock_collection = MagicMock()
    mock_collection.query.return_value = {
        "documents": [["Test content"]],
        "metadatas": [[{"source": "test.txt", "user_id": "default"}]],
        "ids": [["id1"]]
    }
    mock_chroma.PersistentClient.return_value.get_or_create_collection.return_value = mock_collection
    
    from main import app
    import main
    main.chroma_client = mock_chroma.PersistentClient.return_value
    main.embedding_fn = MagicMock()
    
    return TestClient(app)

def test_health_check(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

def test_rag_search_mocked(client: TestClient):
    payload = {
        "query": "test", 
        "k": 1, 
        "collection_name": "nextcloud_files",
        "user_id": "default"
    }
    resp = client.post("/rag/search", json=payload, headers={"X-Internal-Secret": "change-me-in-production"})
    assert resp.status_code == 200
    assert "results" in resp.json()
    assert len(resp.json()["results"]) > 0
    assert resp.json()["results"][0]["content"] == "Test content"
