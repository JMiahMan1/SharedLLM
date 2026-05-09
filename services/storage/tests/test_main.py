import pytest
from fastapi.testclient import TestClient
import os

@pytest.fixture(name="client")
def client_fixture():
    from main import app
    return TestClient(app)

def test_health_check(client: TestClient):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

def test_storage_list_mocked(client: TestClient, monkeypatch):
    from models import StorageEntry
    # Mocking providers.build_provider to return a dummy list
    class MockProvider:
        def list_entries(self, path="/", recursive=False):
            return [StorageEntry(name="test_file.txt", path="test_file.txt", is_dir=False)]
    
    monkeypatch.setattr("main.build_provider", lambda config: MockProvider())
    
    # We need a valid ProviderConfig in the payload
    payload = {
        "path": "/",
        "provider": {
            "kind": "nextcloud",
            "settings": {"url": "http://fake", "username": "u", "password": "p"}
        }
    }
    resp = client.post("/providers/list", json=payload, headers={"X-Internal-Secret": "change-me-in-production"})
    assert resp.status_code == 200
    assert "entries" in resp.json()
    assert resp.json()["entries"][0]["name"] == "test_file.txt"
