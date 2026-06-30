import os
import sys
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

# Mock heavy ML dependencies before importing the app
# Build proper module hierarchy for chromadb
_mock_chromadb = MagicMock()
_mock_chromadb.api = MagicMock()
_mock_chromadb.api.types = MagicMock()
_mock_chromadb.config = MagicMock()
_mock_chromadb.utils = MagicMock()
_mock_chromadb.api.types.EmbeddingFunction = MagicMock()
sys.modules['chromadb'] = _mock_chromadb
sys.modules['chromadb.api'] = _mock_chromadb.api
sys.modules['chromadb.api.types'] = _mock_chromadb.api.types
sys.modules['chromadb.config'] = _mock_chromadb.config
sys.modules['chromadb.utils'] = _mock_chromadb.utils
sys.modules['sentence_transformers'] = MagicMock()

# Ensure parent directory is in sys.path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["INTERNAL_SECRET"] = "test-secret"
from main import app

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
