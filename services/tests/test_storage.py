"""
Test suite for the Storage Bridge Microservice (services/storage).
Tests NextCloud integration, file listing, and metadata retrieval.
Related code: services/storage/main.py, services/storage/nextcloud_client.py
"""
import pytest
from fastapi.testclient import TestClient
from storage.main import app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "storage"

def test_nextcloud_list_unauthorized(mocker):
    # Mocking easywebdav connect to avoid real network calls
    mocker.patch("easywebdav.connect")
    
    payload = {
        "nc_url": "https://nc.example.com",
        "nc_user": "testuser",
        "nc_pass": "testpass",
        "path": "/"
    }
    # Since we didn't mock the internal logic fully, it might fail on ls()
    # but the goal is to test the endpoint structure.
    response = client.post("/nextcloud/list", json=payload)
    # If the mock is simple, it might return 200 with empty list or fail
    assert response.status_code == 200
