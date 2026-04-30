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

client = TestClient(app)

@pytest.fixture
def mock_intent(mocker):
    """Mock the semantic router."""
    return mocker.patch("gateway.main.engine.classify")

def test_gateway_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "gateway"

def test_fast_path_light_control(mock_intent, mocker):
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


def test_slow_path_conversational(mock_intent, mocker):
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
