import asyncio
import os
import sys
from fastapi.testclient import TestClient

# Ensure parent directory is in sys.path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["INTERNAL_SECRET"] = "test-secret"

from main import app, health, list_provider_entries, write_provider_content
from main import IndexScanRequest
from models import ProviderWriteRequest, StorageEntry
from storage.providers import ProviderConfig

client = TestClient(app)

class FakeProvider:
    def __init__(self, entries):
        self.entries = entries
        self.writes = []

    def list_entries(self, path="/", recursive=False):
        return self.entries

    def write_content(self, path, content, create_parents=True, verify=True, is_binary=False):
        self.writes.append({
            "path": path, "content": content, "create_parents": create_parents, 
            "verify": verify, "is_binary": is_binary
        })
        size = len(content) if isinstance(content, bytes) else len(content.encode("utf-8"))
        return {"path": path, "bytes_written": size, "verified": verify}

def _fixture_entries():
    return [
        StorageEntry(path="/Library/ProjectAlpha/README.md", name="README.md", is_dir=False, content_type="text/markdown"),
        StorageEntry(path="/Library/Media/song.mp3", name="song.mp3", is_dir=False, content_type="audio/mpeg"),
    ]

def test_health():
    response = health()
    assert response["service"] == "storage"

def test_provider_list_returns_generic_entries(monkeypatch):
    monkeypatch.setattr("main.build_provider", lambda config: FakeProvider(_fixture_entries()))

    request = IndexScanRequest(
        provider=ProviderConfig(kind="nextcloud", settings={"url": "https://cloud.local", "username": "jeremiah", "password": "secret"}),
        path="/Library",
        recursive=False,
    )

    data = asyncio.run(list_provider_entries(request))
    assert data["status"] == "SUCCESS"
    assert len(data["entries"]) == 2  # type: ignore[arg-type]

def test_provider_write_returns_result(monkeypatch):
    fake_provider = FakeProvider(_fixture_entries())
    monkeypatch.setattr("main.build_provider", lambda config: fake_provider)

    request = ProviderWriteRequest(
        provider=ProviderConfig(kind="nextcloud", settings={"url": "https://cloud.local", "username": "jeremiah", "password": "secret"}),
        path="/docs/example.md",
        content="# Example\n",
        create_parents=True,
        verify=True,
    )

    data = asyncio.run(write_provider_content(request))
    assert data["status"] == "SUCCESS"
    assert data["result"]["path"] == "/docs/example.md"  # type: ignore[call-overload]
