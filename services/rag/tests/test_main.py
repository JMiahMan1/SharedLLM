import os
import sys
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

# Mock heavy ML dependencies before importing the app
sys.modules['chromadb'] = MagicMock()
sys.modules['chromadb.config'] = MagicMock()
sys.modules['chromadb.utils'] = MagicMock()
sys.modules['sentence_transformers'] = MagicMock()

# Ensure parent directory is in sys.path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["INTERNAL_SECRET"] = "test-secret"
from main import app
import main

client = TestClient(app)

def test_ingest_and_search(mocker):
    mock_collection = mocker.Mock()
    mock_collection.query.return_value = {
        "documents": [["Test doc content"]],
        "metadatas": [[{"user_id": "alice", "source": "test"}]]
    }
    
    mocker.patch("main.get_collection", return_value=mock_collection)
    
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
