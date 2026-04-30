import os
"""
Test suite for the Execution Bridge Microservice (services/execution).
Tests Home Assistant integration, command dispatching, and entity state fetching.
Related code: services/execution/main.py, services/execution/ha_client.py
"""
import pytest
from fastapi.testclient import TestClient

os.environ["INTERNAL_SECRET"] = "test-secret"
from execution.main import app

client = TestClient(app)

# Common valid user context
valid_context = {
    "user": "testuser",
    "ha_url": "http://ha.local",
    "ha_token": "mock-token"
}

def test_missing_internal_secret():
    """Ensure the execution bridge drops requests without the internal secret."""
    resp = client.post("/execute/light", json={
        "user_context": valid_context,
        "entity_id": "light.living_room",
        "action": "turn_on"
    })
    assert resp.status_code == 403  # Forbidden

def test_light_control_valid(mocker):
    """Test valid light payload triggers the HA client correctly."""
    mock_call = mocker.patch("execution.main.ha_client.call_service", return_value={"ok": True, "status_code": 200})
    
    resp = client.post("/execute/light", 
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "entity_id": "light.living_room",
            "action": "turn_on",
            "brightness_pct": 80
        }
    )
    
    assert resp.status_code == 200
    assert resp.json()["status"] == "SUCCESS"
    
    # Verify the HA client was called with correct parameters
    mock_call.assert_called_once()
    args, _ = mock_call.call_args
    assert args[2] == "light" # domain
    assert args[3] == "turn_on" # service
    assert args[4] == "light.living_room" # entity_id

def test_light_control_invalid_brightness():
    """Test Pydantic bounds checking (brightness must be 0-100)."""
    resp = client.post("/execute/light", 
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "entity_id": "light.living_room",
            "action": "turn_on",
            "brightness_pct": 150 # Invalid
        }
    )
    assert resp.status_code == 422

def test_media_play_valid(mocker):
    """Test valid media play payload."""
    mock_call = mocker.patch("execution.main.ha_client.call_service", return_value={"ok": True})
    
    resp = client.post("/execute/media/play", 
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "entity_id": "media_player.kitchen",
            "query": "The Beatles"
        }
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "SUCCESS"

def test_tv_cast_smart_power_sync(mocker):
    """Test the SmartPowerSync pattern where the TV is powered on first."""
    # First mock return gets the state (TV is off), second mock return is the power on, third is play_media
    mock_state = mocker.patch("execution.main.ha_client.get_state", return_value={"state": "off"})
    mock_call = mocker.patch("execution.main.ha_client.call_service", return_value={"ok": True})
    
    # Mock sleep so tests don't actually pause
    mocker.patch("asyncio.sleep", return_value=None)
    
    resp = client.post("/execute/tv_cast", 
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "media_player_entity_id": "media_player.living_room_tv",
            "media_content_id": "https://youtube.com/watch?v=123",
            "power_on_wait_ms": 100 # Low wait for testing, though mocked
        }
    )
    assert resp.status_code == 200
    assert mock_call.call_count == 2 # power on + play
