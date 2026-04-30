"""
Test suite for the Storage Bridge Microservice (services/storage).
Tests provider-agnostic content indexing, capability mapping, and Nextcloud-compatible listing.
Related code: services/storage/main.py, services/storage/indexer.py, services/storage/providers.py
"""

import pytest

from storage.main import (
    health,
    list_nextcloud_compat,
    list_provider_entries,
    scan_content_index,
    search_nextcloud_compat,
)
from storage.models import IndexScanRequest, ProviderListRequest, StorageEntry


class FakeProvider:
    def __init__(self, entries):
        self.entries = entries

    def list_entries(self, path="/", recursive=False):
        return self.entries


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


@pytest.mark.asyncio
async def test_index_scan_classifies_common_content(monkeypatch):
    monkeypatch.setattr("storage.main.build_provider", lambda config: FakeProvider(_fixture_entries()))

    request = IndexScanRequest(
        provider={
            "kind": "nextcloud",
            "settings": {"url": "https://cloud.local", "username": "jeremiah", "password": "secret"},
        },
        path="/Library",
        recursive=True,
    )

    data = await scan_content_index(request)
    assert data["status"] == "SUCCESS"
    assert data["summary"]["total_items"] == len(_fixture_entries())

    items = _index_by_path(data["items"])

    project = items["/Library/ProjectAlpha"]
    assert project["subtype"] == "git_repo"
    assert "repo_scanner" in project["recommended_tools"]
    assert "git_metadata" in project["extractable_capabilities"]

    notes = items["/Library/NotesVault"]
    assert notes["subtype"] == "notes_vault"
    assert "notes" in notes["recommended_tools"]

    markdown = items["/Library/ProjectAlpha/README.md"]
    assert markdown["item_type"] == "markdown"
    assert "rag" in markdown["recommended_tools"]

    docx = items["/Library/Docs/plan.docx"]
    assert docx["subtype"] == "word_processing"
    assert "document_parser" in docx["recommended_tools"]

    sheet = items["/Library/Docs/budget.xlsx"]
    assert sheet["subtype"] == "spreadsheet"
    assert "table_parser" in sheet["recommended_tools"]

    audio = items["/Library/Media/song.mp3"]
    assert audio["item_type"] == "audio"
    assert "transcription" in audio["extractable_capabilities"]

    video = items["/Library/Media/movie.mp4"]
    assert video["item_type"] == "video"
    assert "visual_description" in video["extractable_capabilities"]

    image = items["/Library/Media/cover.png"]
    assert image["item_type"] == "image"
    assert "ocr" in image["recommended_tools"]

    ebook = items["/Library/Books/novel.epub"]
    assert ebook["item_type"] == "ebook"
    assert "ebook_parser" in ebook["recommended_tools"]


@pytest.mark.asyncio
async def test_provider_list_returns_generic_entries(monkeypatch):
    monkeypatch.setattr("storage.main.build_provider", lambda config: FakeProvider(_fixture_entries()[:3]))

    request = ProviderListRequest(
        provider={
            "kind": "nextcloud",
            "settings": {"url": "https://cloud.local", "username": "jeremiah", "password": "secret"},
        },
        path="/Library",
        recursive=False,
    )

    data = await list_provider_entries(request)
    assert data["status"] == "SUCCESS"
    assert data["provider"] == "nextcloud"
    assert len(data["entries"]) == 3


@pytest.mark.asyncio
async def test_nextcloud_list_compatibility_shim(monkeypatch):
    monkeypatch.setattr("storage.main.build_provider", lambda config: FakeProvider(_fixture_entries()[:2]))

    data = await list_nextcloud_compat(
        {
        "nc_url": "https://cloud.local",
        "nc_user": "jeremiah",
        "nc_pass": "secret",
        "path": "/Library",
        }
    )
    assert data["status"] == "SUCCESS"
    assert len(data["files"]) == 2


@pytest.mark.asyncio
async def test_nextcloud_search_compatibility_shim(monkeypatch):
    monkeypatch.setattr("storage.main.build_provider", lambda config: FakeProvider(_fixture_entries()))

    data = await search_nextcloud_compat(
        {
        "nc_url": "https://cloud.local",
        "nc_user": "jeremiah",
        "nc_pass": "secret",
        "path": "/Library",
        },
        query="novel",
    )
    assert data["status"] == "SUCCESS"
    assert len(data["matches"]) == 1
    assert data["matches"][0]["name"] == "novel.epub"
