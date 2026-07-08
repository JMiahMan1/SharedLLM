import os

from fastapi.testclient import TestClient

os.environ.setdefault("INTERNAL_SECRET", "test-secret")

from services.storage.main import app, health
from services.storage.providers import StorageProvider

client = TestClient(app)


class FakeProvider(StorageProvider):
    def __init__(self, entries):
        self._entries = entries
        self.writes = []

    def list_entries(self, path="/", recursive=False):
        return self._entries

    def write_content(self, path, content, create_parents=True, verify=True, is_binary=False):
        self.writes.append({
            "path": path, "content": content, "create_parents": create_parents,
            "verify": verify, "is_binary": is_binary,
        })
        size = len(content) if isinstance(content, bytes) else len(content.encode("utf-8"))
        return {"path": path, "bytes_written": size, "verified": verify}

    def upload_directory(self, remote_path, local_path, excludes=None):
        return {"status": "SUCCESS", "files_uploaded": 0}


def test_health():
    response = health()
    assert response["service"] == "storage"


def test_health_via_client():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "storage"
    assert data["status"] == "ok"
