import pytest
import asyncio
from unittest.mock import MagicMock
from fastapi import BackgroundTasks

from services.storage.indexer import chunk_text, CheckpointManager, extract_and_chunk_contents
from services.storage.models import ContentIndexItem
from services.storage.main import IndexScanRequest

@pytest.mark.server_only
def test_storage_main_functions():
    from services.storage.main import full_content_index, pause_indexing, resume_indexing
    assert callable(full_content_index)
    assert callable(pause_indexing)
    assert callable(resume_indexing)

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

def test_extract_and_chunk_contents_logic():
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
    
    chunks = asyncio.run(extract_and_chunk_contents(mock_provider, items))
    assert len(chunks) == 2
    assert chunks[0]["metadata"]["path"] == "/test.txt"
    assert chunks[0]["metadata"]["is_dir"] is False
    assert chunks[1]["content"] == "Hello world knowledge"
    assert chunks[1]["metadata"]["path"] == "/test.txt"

@pytest.mark.server_only
def test_storage_api_control_endpoints():
    from services.storage.main import pause_indexing, resume_indexing
    resp = pause_indexing()
    assert resp["status"] == "PAUSED"

    resp = resume_indexing()
    assert resp["status"] == "RESUMED"

@pytest.mark.server_only
def test_full_index_endpoint_mocks(monkeypatch):
    from services.storage.models import ProviderConfig
    from services.storage.main import full_content_index
    request = IndexScanRequest(
        provider=ProviderConfig(kind="nextcloud", settings={"url": "http://x", "username": "u", "password": "p"}),
        path="/",
        recursive=True,
    )
    data = asyncio.run(full_content_index(request, BackgroundTasks()))
    assert data["status"] == "SUCCESS"
    assert data["message"] == "Indexing started in background."
