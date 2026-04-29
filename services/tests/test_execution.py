import os
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
    assert resp.status_code == 422  # Missing header validation failure

def test_light_control_valid(mocker):
    """Test valid light payload triggers the HA client correctly."""
    mock_run = mocker.patch("execution.main._run", return_value={"ok": True, "status_code": 200})
    
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
    assert resp.json()["service"] == "light_control"
    
    # Verify the HA client was called with correct parameters
    mock_run.assert_called_once()
    args, _ = mock_run.call_args
    assert args[3] == "light" # domain
    assert args[4] == "turn_on" # service
    assert args[5] == "light.living_room" # entity_id

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
    assert "less than or equal to 100" in resp.text

def test_media_play_valid(mocker):
    """Test valid media play payload."""
    mock_run = mocker.patch("execution.main._run", return_value={"ok": True})
    
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
    mock_run = mocker.patch("execution.main._run", side_effect=[
        {"state": "off"}, # get_state
        {"ok": True},     # turn_on
        {"ok": True}      # play_media
    ])
    
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
    assert mock_run.call_count == 3
