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

def test_ha_entities_mocked(client: TestClient, monkeypatch):
    # Mocking ha_client functions
    async def mock_get_states(url, token):
        return [{"entity_id": "light.kitchen", "state": "on"}]
    async def mock_get_areas(url, token):
        return {"light.kitchen": "Kitchen"}
    
    import main
    monkeypatch.setattr(main.ha_client, "get_states", mock_get_states)
    monkeypatch.setattr(main.ha_client, "get_areas", mock_get_areas)
    
    params = {"ha_url": "http://ha", "ha_token": "token"}
    resp = client.get("/discovery/entities", params=params, headers={"X-Internal-Secret": "change-me-in-production"})
    assert resp.status_code == 200
    assert "entities" in resp.json()
    assert resp.json()["entities"][0]["entity_id"] == "light.kitchen"
