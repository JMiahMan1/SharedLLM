"""
Test suite for the Intent Gateway Microservice (services/gateway).
Tests semantic routing (Fast Path vs Slow Path), identity integration, and error propagation.
Related code: services/gateway/main.py, services/gateway/intent_engine.py, services/gateway/schemas.py
"""
import os
import pytest
from fastapi.testclient import TestClient
import httpx

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FAST_PATH_THRESHOLD"] = "0.85"
os.environ["IDENTITY_SERVICE_URL"] = "http://identity"
os.environ["EXECUTION_SERVICE_URL"] = "http://execution"
os.environ["OLLAMA_URL"] = "http://ollama"

from gateway.main import app

@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client

@pytest.fixture
def mock_intent(mocker):
    """Mock the semantic router."""
    return mocker.patch("gateway.main.engine.classify")

def test_gateway_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "gateway"

def test_fast_path_light_control(client, mock_intent, mocker):
    """Test the Gateway Fast Path successfully routes a turn_on intent to Execution."""
    # Mock high confidence intent
    mock_intent.return_value = ("turn_on", 0.95)
    
    # Mock the internal calls using mocker.patch on the specific functions
    mocker.patch("gateway.main.resolve_identity", return_value={"user": "alice", "ha_url": "http://ha.local", "ha_token": "tok"})
    mocker.patch("gateway.main.fetch_ha_entities", return_value=[{"entity_id": "light.living_room"}])
    mocker.patch("gateway.main.execute_command", return_value={"status": "SUCCESS", "message": "Lights on", "service": "light_control"})
    mocker.patch("gateway.main.update_history", return_value=None)
    mocker.patch("gateway.main.get_history", return_value=[])

    resp = client.post("/api/chat", json={
        "query": "Turn on the living room lights",
        "voice_id": "alice"
    })
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["intent"] == "turn_on"
    assert data["execution_result"]["service"] == "light_control"


def test_slow_path_conversational(client, mock_intent, mocker):
    """Test the Gateway Slow Path when confidence is low or intent is unknown."""
    mock_intent.return_value = ("unknown", 0.40)
    
    mocker.patch("gateway.main.resolve_identity", return_value={"user": "alice"})
    mocker.patch("gateway.main.fetch_ha_entities", return_value=[])
    mocker.patch("gateway.main.get_history", return_value=[])
    mocker.patch("gateway.main.update_history", return_value=None)
    mocker.patch("gateway.main.contextualize_query", return_value="What is the meaning of life?")
    mocker.patch("gateway.main.decompose_command_query", return_value=[])
    
    # Mock Ollama call directly in httpx
    class MockResponse:
        def __init__(self, json_data):
            self.json_data = json_data
            self.status_code = 200
        def json(self): return self.json_data
        def raise_for_status(self): pass

    async def mock_post(*args, **kwargs):
        return MockResponse({"message": {"content": "Simulated LLM response"}})

    mocker.patch("httpx.AsyncClient.post", side_effect=mock_post)

    resp = client.post("/api/chat", json={
        "query": "What is the meaning of life?",
        "voice_id": "alice"
    })
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert "Simulated LLM response" in data["message"]


def test_action_with_status_followup_executes_and_reports_refreshed_state(client, mock_intent, mocker):
    mock_intent.return_value = ("turn_off", 0.95)

    mocker.patch("gateway.main.resolve_identity", return_value={"user": "alice", "ha_url": "http://ha.local", "ha_token": "tok"})
    mocker.patch("gateway.main.get_history", return_value=[])
    mocker.patch("gateway.main.update_history", return_value=None)
    mocker.patch("gateway.main.contextualize_query", return_value="Can you power off the Piano-Lamp and recheck its status after?")
    mocker.patch("gateway.main.execute_command", return_value={"status": "SUCCESS", "message": "Powered off Piano-Lamp."})

    fetch_entities = mocker.patch("gateway.main.fetch_ha_entities")
    fetch_entities.side_effect = [
        [{"entity_id": "light.piano_lamp", "state": "on", "attributes": {"friendly_name": "Piano-Lamp"}}],
        [{"entity_id": "light.piano_lamp", "state": "off", "attributes": {"friendly_name": "Piano-Lamp"}}],
    ]

    resp = client.post(
        "/api/chat",
        json={"query": "Can you power off the Piano-Lamp and recheck its status after?", "voice_id": "alice"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert "Powered off Piano-Lamp." in data["message"]["content"]
    assert "Current status of Piano-Lamp is off." in data["message"]["content"]
