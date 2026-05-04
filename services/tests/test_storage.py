"""
Test suite for the Storage Bridge Microservice (services/storage).
Tests provider-agnostic content indexing, capability mapping, and Nextcloud-compatible listing.
Related code: services/storage/main.py, services/storage/indexer.py, services/storage/providers.py
"""

import asyncio

import pytest

from storage.main import (
    health,
    list_provider_entries,
    search_provider,
    write_provider_content,
)
from storage.indexer import build_content_index, summarize_index
from storage.models import IndexScanRequest, ProviderListRequest, ProviderWriteRequest, StorageEntry


class FakeProvider:
    def __init__(self, entries):
        self.entries = entries
        self.writes = []

    def list_entries(self, path="/", recursive=False):
        return self.entries

    def write_content(self, path, content, create_parents=True, verify=True):
        self.writes.append(
            {
                "path": path,
                "content": content,
                "create_parents": create_parents,
                "verify": verify,
            }
        )
        return {"path": path, "bytes_written": len(content.encode("utf-8")), "verified": verify}


def _fixture_entries():
    return [
        StorageEntry(path="/Library", name="Library", is_dir=True),
        StorageEntry(path="/Library/ProjectAlpha", name="ProjectAlpha", is_dir=True),
        StorageEntry(path="/Library/ProjectAlpha/.git", name=".git", is_dir=True),
        StorageEntry(path="/Library/ProjectAlpha/README.md", name="README.md", is_dir=False, content_type="text/markdown"),
        StorageEntry(path="/Library/ProjectAlpha/main.py", name="main.py", is_dir=False, content_type="text/x-python"),
        StorageEntry(path="/Library/NotesVault", name="NotesVault", is_dir=True),
        StorageEntry(path="/Library/NotesVault/.obsidian", name=".obsidian", is_dir=True),
        StorageEntry(path="/Library/NotesVault/daily.md", name="daily.md", is_dir=False, content_type="text/markdown"),
        StorageEntry(path="/Library/Docs", name="Docs", is_dir=True),
        StorageEntry(path="/Library/Docs/plan.docx", name="plan.docx", is_dir=False, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        StorageEntry(path="/Library/Docs/budget.xlsx", name="budget.xlsx", is_dir=False, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        StorageEntry(path="/Library/Media", name="Media", is_dir=True),
        StorageEntry(path="/Library/Media/song.mp3", name="song.mp3", is_dir=False, content_type="audio/mpeg"),
        StorageEntry(path="/Library/Media/movie.mp4", name="movie.mp4", is_dir=False, content_type="video/mp4"),
        StorageEntry(path="/Library/Media/cover.png", name="cover.png", is_dir=False, content_type="image/png"),
        StorageEntry(path="/Library/Books", name="Books", is_dir=True),
        StorageEntry(path="/Library/Books/novel.epub", name="novel.epub", is_dir=False, content_type="application/epub+zip"),
    ]


def _index_by_path(items):
    return {item["path"]: item for item in items}


def test_health():
    response = health()
    assert response["service"] == "storage"


def test_build_content_index_matches_current_rules():
    data = build_content_index(_fixture_entries())
    summary = summarize_index(data)
    items = _index_by_path([item.dict() for item in data])

    assert "/Library/ProjectAlpha/.git" not in items
    assert "/Library/NotesVault/.obsidian" in items
    assert summary["total_items"] == len(items)

    project = items["/Library/ProjectAlpha"]
    assert project["item_type"] == "folder"
    assert project["subtype"] == "generic_directory"

    markdown = items["/Library/ProjectAlpha/README.md"]
    assert markdown["item_type"] == "document"
    assert markdown["subtype"] == "markdown"
    assert "rag" in markdown["recommended_tools"]
    assert "structure_scan" in markdown["extractable_capabilities"]

    docx = items["/Library/Docs/plan.docx"]
    assert docx["subtype"] == "word_processing"
    assert "document_parser" in docx["recommended_tools"]

    binary_examples = [
        "/Library/Docs/budget.xlsx",
        "/Library/Media/song.mp3",
        "/Library/Media/movie.mp4",
        "/Library/Media/cover.png",
        "/Library/Books/novel.epub",
    ]
    for path in binary_examples:
        assert items[path]["item_type"] == "binary"
        assert items[path]["subtype"] == "unknown_binary"
        assert items[path]["recommended_tools"] == ["media"]


def test_provider_list_returns_generic_entries(monkeypatch):
    monkeypatch.setattr("storage.main.build_provider", lambda config: FakeProvider(_fixture_entries()[:3]))

    request = ProviderListRequest(
        provider={
            "kind": "nextcloud",
            "settings": {"url": "https://cloud.local", "username": "jeremiah", "password": "secret"},
        },
        path="/Library",
        recursive=False,
    )

    data = asyncio.run(list_provider_entries(request))
    assert data["status"] == "SUCCESS"
    assert data["count"] == 3
    assert len(data["entries"]) == 3


def test_provider_search_returns_matches(monkeypatch):
    monkeypatch.setattr("storage.main.build_provider", lambda config: FakeProvider(_fixture_entries()))
    
    async def fake_run_in_threadpool(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr("starlette.concurrency.run_in_threadpool", fake_run_in_threadpool)

    request = IndexScanRequest(
        provider={
            "kind": "nextcloud",
            "settings": {"url": "https://cloud.local", "username": "jeremiah", "password": "secret"},
        },
        path="/Library",
        recursive=True,
    )

    data = asyncio.run(search_provider(query="novel", req=request))
    assert data["status"] == "SUCCESS"
    assert len(data["matches"]) == 1
    assert data["matches"][0]["name"] == "novel.epub"


def test_provider_write_returns_result(monkeypatch):
    fake_provider = FakeProvider(_fixture_entries())
    monkeypatch.setattr("storage.main.build_provider", lambda config: fake_provider)

    request = ProviderWriteRequest(
        provider={
            "kind": "nextcloud",
            "settings": {"url": "https://cloud.local", "username": "jeremiah", "password": "secret"},
        },
        path="/Code/SharedLLM/docs/example.md",
        content="# Example\n",
        create_parents=True,
        verify=True,
    )

    data = asyncio.run(write_provider_content(request))
    assert data["status"] == "SUCCESS"
    assert data["result"]["path"] == "/Code/SharedLLM/docs/example.md"
    assert fake_provider.writes[0]["verify"] is True
