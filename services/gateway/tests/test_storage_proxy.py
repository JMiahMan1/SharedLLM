import pytest
import respx
import httpx
from fastapi.testclient import TestClient
from services.gateway.main import app, IDENTITY_SVC, STORAGE_SVC, RAG_SVC, INTERNAL_SECRET

client = TestClient(app)

@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-token"}

@respx.mock
def test_storage_list_proxy(auth_headers):
    # Mock identity resolution
    respx.post(f"{IDENTITY_SVC}/api/resolve").mock(
        return_value=httpx.Response(200, json={
            "user": "testuser",
            "nextcloud_url": "http://nc.local",
            "nextcloud_user": "ncuser",
            "nextcloud_pass": "ncpass"
        })
    )
    
    # Mock storage service call
    respx.post(f"{STORAGE_SVC}/providers/list").mock(
        return_value=httpx.Response(200, json={
            "status": "SUCCESS",
            "entries": [{"path": "/test.txt", "name": "test.txt", "is_dir": False}]
        })
    )
    
    response = client.post("/api/storage/list", json={"path": "/"}, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCESS"
    assert len(data["entries"]) == 1
    
    # Verify internal secret and credentials were sent to storage
    last_request = respx.calls.last.request
    assert last_request.headers["X-Internal-Secret"] == INTERNAL_SECRET
    payload = last_request.content
    import json
    sent_payload = json.loads(payload)
    assert sent_payload["provider"]["settings"]["username"] == "ncuser"
    assert sent_payload["provider"]["settings"]["password"] == "ncpass"

@respx.mock
def test_storage_index_proxy(auth_headers):
    # Mock identity resolution
    respx.post(f"{IDENTITY_SVC}/api/resolve").mock(
        return_value=httpx.Response(200, json={
            "user": "testuser",
            "nextcloud_url": "http://nc.local",
            "nextcloud_user": "ncuser",
            "nextcloud_pass": "ncpass"
        })
    )
    
    # Mock storage indexing call
    respx.post(f"{STORAGE_SVC}/index/full").mock(
        return_value=httpx.Response(202, json={"status": "ACCEPTED"})
    )
    
    response = client.post("/api/storage/index", json={"path": "/"}, headers=auth_headers)
    assert response.status_code == 202
    assert response.json()["status"] == "ACCEPTED"

@respx.mock
def test_storage_stats_proxy():
    # Mock identity resolution (called by _resolve_identity_from_request)
    respx.post(f"{IDENTITY_SVC}/api/resolve").mock(
        return_value=httpx.Response(200, json={
            "user": "testuser",
            "nextcloud_user": "ncuser"
        })
    )
    
    # Mock RAG stats call
    respx.get(f"{RAG_SVC}/rag/stats").mock(
        return_value=httpx.Response(200, json={"total_chunks": 100, "total_documents": 10})
    )
    
    response = client.get("/api/storage/stats")
    assert response.status_code == 200
    assert response.json()["total_chunks"] == 100
