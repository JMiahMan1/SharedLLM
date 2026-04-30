"""
Test suite for the Intent Gateway Microservice (services/gateway).
Tests semantic routing (Fast Path vs Slow Path), identity integration, and error propagation.
Related code: services/gateway/main.py, services/gateway/intent_engine.py, services/gateway/schemas.py
"""
import os
import pytest
from fastapi.testclient import TestClient

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["FAST_PATH_THRESHOLD"] = "0.85"

from gateway.main import app

client = TestClient(app)

@pytest.fixture
def mock_identity(mocker):
    """Mock the Identity Service resolution."""
    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "user": "alice",
        "ha_url": "http://ha.local",
        "ha_token": "secret-token"
    }
    mock_resp.raise_for_status = mocker.Mock()
    
    return mocker.patch("httpx.AsyncClient.post", return_value=mock_resp)


@pytest.fixture
def mock_intent(mocker):
    """Mock the semantic router."""
    return mocker.patch("gateway.main.engine.classify")

def test_gateway_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "gateway"

def test_fast_path_light_control(mock_identity, mock_intent, mocker):
    """Test the Gateway Fast Path successfully routes a turn_on intent to Execution."""
    # Mock high confidence intent
    mock_intent.return_value = ("turn_on", 0.95)
    
    # We need a separate mock specifically for the execution call since httpx.post is called twice
    # First for identity, second for execution
    # We use AsyncMock for the base request method. httpx.AsyncClient.post/get call this internally.
    async def mock_http_side_effect(method, url, **kwargs):
        resp = mocker.Mock()
        resp.status_code = 200
        if method == "POST":
            if "resolve" in url:
                resp.json.return_value = {"user": "alice", "ha_url": "http", "ha_token": "tok"}
            else:
                resp.json.return_value = {"status": "SUCCESS", "message": "Lights on", "service": "light_control"}
        elif method == "GET":
            if "entities" in url:
                resp.json.return_value = [
                    {"entity_id": "light.living_room", "state": "off", "attributes": {"friendly_name": "living room lights"}}
                ]
        resp.raise_for_status = mocker.Mock()
        return resp
        
    mocker.patch("httpx.AsyncClient.request", side_effect=mock_http_side_effect)

    resp = client.post("/api/chat", json={
        "query": "Turn on the living room lights",
        "voice_id": "alice"
    })
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["intent"] == "turn_on"
    assert data["confidence"] == 0.95
    assert data["execution_result"]["service"] == "light_control"


def test_slow_path_conversational(mock_intent, mocker):
    """Test the Gateway Slow Path when confidence is low or intent is unknown."""
    mock_intent.return_value = ("unknown", 0.40)
    
    async def mock_request(method, url, *args, **kwargs):
        resp = mocker.Mock()
        resp.status_code = 200
        if "resolve" in url:
            resp.json.return_value = {"user": "alice", "ha_url": "http", "ha_token": "tok"}
        elif "rag/search" in url:
            resp.json.return_value = {"results": [{"content": "Doc 1", "metadata": {}}]}
        elif "entities" in url:
            resp.json.return_value = []
        resp.raise_for_status = mocker.Mock()
        return resp
        
    mocker.patch("httpx.AsyncClient.request", side_effect=mock_request)

    resp = client.post("/api/chat", json={
        "query": "What is the meaning of life?",
        "voice_id": "alice"
    })
    
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert "Simulated LLM response" in data["message"]
    assert data["intent"] == "unknown"
    assert data["execution_result"] is None

def test_identity_resolution_failure(mocker):
    """Test Gateway rejects chat if Identity Service fails to resolve user."""
    async def mock_request(method, url, *args, **kwargs):
        resp = mocker.Mock()
        resp.status_code = 404
        resp.json.return_value = {"detail": "User not found"}
        return resp
        
    mocker.patch("httpx.AsyncClient.request", side_effect=mock_request)
    
    resp = client.post("/api/chat", json={
        "query": "Turn on the lights",
        "voice_id": "unknown_intruder"
    })
    
    assert resp.status_code == 401
    assert resp.json()["detail"] == "User resolution failed"
