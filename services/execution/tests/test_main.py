import os
import sys
import pytest
from fastapi.testclient import TestClient

# Ensure parent directory is in sys.path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["INTERNAL_SECRET"] = "test-secret"
from main import app
import main

client = TestClient(app)

valid_context = {
    "user": "testuser",
    "ha_url": "http://ha.local",
    "ha_token": "mock-token"
}

def test_missing_internal_secret():
    resp = client.post("/execute/light", json={
        "user_context": valid_context,
        "entity_id": "light.living_room",
        "action": "turn_on"
    })
    assert resp.status_code == 403

def test_light_control_valid(mocker):
    mock_call = mocker.patch("main.ha_client.call_service", return_value={"ok": True, "status_code": 200})
    
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
    mock_call.assert_called_once()

def test_media_play_valid(mocker):
    mock_call = mocker.patch("main.ha_client.call_service", return_value={"ok": True})
    
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
    mocker.patch("main.ha_client.get_state", return_value={"state": "off"})
    mock_call = mocker.patch("main.ha_client.call_service", return_value={"ok": True})
    mocker.patch("asyncio.sleep", return_value=None)
    
    resp = client.post("/execute/tv_cast", 
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "media_player_entity_id": "media_player.living_room_tv",
            "media_content_id": "https://youtube.com/watch?v=123",
            "power_on_wait_ms": 100
        }
    )
    assert resp.status_code == 200
    assert mock_call.call_count >= 1

def test_entity_search_by_query(mocker):
    mock_states = mocker.patch("main.ha_client.get_states", return_value=[
        {"entity_id": "media_player.office_tv", "state": "idle", "attributes": {"friendly_name": "Office TV", "device_class": "tv"}},
        {"entity_id": "media_player.office_tv_chrome", "state": "off", "attributes": {"friendly_name": "Office TV Cast", "device_class": "speaker"}},
        {"entity_id": "light.office_desk", "state": "on", "attributes": {"friendly_name": "Office Desk Light", "device_class": "light"}},
    ])
    
    resp = client.post("/execute/entity/search",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "query": "office tv",
            "domain": "media_player"
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    entities = data["detail"]["entities"]
    assert len(entities) >= 1
    assert entities[0]["entity_id"].startswith("media_player.")
    assert "office" in entities[0]["entity_id"].lower() or "tv" in entities[0]["entity_id"].lower()

def test_entity_search_no_results(mocker):
    mocker.patch("main.ha_client.get_states", return_value=[
        {"entity_id": "light.kitchen", "state": "off", "attributes": {"friendly_name": "Kitchen Light", "device_class": "light"}},
    ])
    
    resp = client.post("/execute/entity/search",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "query": "nonexistent device xyz",
            "domain": "media_player"
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert len(data["detail"]["entities"]) == 0
