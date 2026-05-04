import pytest
import os
import json
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient

# These work with PYTHONPATH=services
from storage.indexer import chunk_text, build_content_index, CheckpointManager, extract_and_chunk_contents
from storage.models import StorageEntry, IndexScanRequest, ContentIndexItem
from storage.main import app

client = TestClient(app)

def test_advanced_chunk_text():
    text = "A" * 1500
    # chunk_size=1000, overlap=200
    chunks = chunk_text(text, chunk_size=1000, overlap=200)
    assert len(chunks) == 2
    assert len(chunks[0]) == 1000
    assert len(chunks[1]) == 700 

def test_advanced_checkpoint_manager(tmp_path):
    checkpoint_file = tmp_path / "checkpoint.json"
    mgr = CheckpointManager(checkpoint_file=str(checkpoint_file))
    
    assert not mgr.is_indexed("/file1.txt", "123")
    
    mgr.mark_indexed("/file1.txt", "123")
    mgr.save()
    
    # Reload
    mgr2 = CheckpointManager(checkpoint_file=str(checkpoint_file))
    assert mgr2.is_indexed("/file1.txt", "123")
    assert not mgr2.is_indexed("/file1.txt", "456")

@pytest.mark.asyncio
async def test_extract_and_chunk_contents_logic():
    # Mock provider
    mock_provider = MagicMock()
    mock_provider.get_content.return_value = "Hello world knowledge"
    
    items = [
        ContentIndexItem(
            path="/test.txt", name="test.txt", is_dir=False, 
            item_type="text", subtype="plain", role="general",
            extractable_capabilities=["full_text"], mtime="123",
            signals=[], recommended_tools=[], restrictions=[], related_items=[], usage_hints=""
        )
    ]
    
    chunks = await extract_and_chunk_contents(mock_provider, items)
    assert len(chunks) == 2
    assert chunks[0]["metadata"]["path"] == "/test.txt"
    assert chunks[0]["metadata"]["is_dir"] is False
    assert chunks[1]["content"] == "Hello world knowledge"
    assert chunks[1]["metadata"]["path"] == "/test.txt"

def test_storage_api_control_endpoints():
    resp = client.post("/index/pause")
    assert resp.status_code == 200
    assert resp.json()["status"] == "PAUSED"
    
    resp = client.post("/index/resume")
    assert resp.status_code == 200
    assert resp.json()["status"] == "RESUMED"

@pytest.mark.asyncio
async def test_full_index_endpoint_mocks(monkeypatch):
    monkeypatch.setattr("storage.main._run_full_index_task", AsyncMock(return_value=None))

    request_payload = {
        "provider": {
            "kind": "nextcloud",
            "settings": {"url": "http://x", "username": "u", "password": "p"}
        },
        "path": "/",
        "recursive": True
    }
    
    resp = client.post("/index/full", json=request_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["message"] == "Indexing started in background."
