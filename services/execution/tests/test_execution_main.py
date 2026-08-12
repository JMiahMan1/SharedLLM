import os

from fastapi.testclient import TestClient

os.environ["INTERNAL_SECRET"] = "test-secret"
os.environ["EXECUTION_EXTERNAL_HOST"] = "localhost"
os.environ["DEVICE_REGISTRY_PATH"] = ":memory:"
from services.execution.main import app

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
    mocker.patch("services.execution.handlers.light.ha_client.call_service", return_value={"ok": True, "status_code": 200})

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

def test_media_play_valid(mocker):
    mocker.patch("services.execution.handlers.media.ha_client.call_service", return_value={"ok": True})

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

def test_media_transport_play_and_volume_set(mocker):
    mocker.patch("services.execution.handlers.media.ha_client.call_service", return_value={"ok": True})

    # Verify play command works without 422
    resp1 = client.post("/execute/media/transport",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "entity_id": "media_player.kitchen",
            "command": "play"
        }
    )
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "SUCCESS"

    # Verify volume_set command works with volume_level parameter
    resp2 = client.post("/execute/media/transport",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "entity_id": "media_player.kitchen",
            "command": "volume_set",
            "volume_level": 0.8
        }
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "SUCCESS"

def test_tv_cast_smart_power_sync(mocker):
    mocker.patch("services.execution.handlers.media.ha_client.get_state", return_value={"state": "off"})
    mocker.patch("services.execution.handlers.media.ha_client.call_service", return_value={"ok": True})
    mocker.patch("asyncio.sleep", return_value=None)
    mocker.patch("services.execution.handlers.video.extract_video_url", return_value="https://example.com/video.mp4")
    mocker.patch("services.execution.handlers.video.download_video_progressive", return_value=("test_media_id", "Test Video"))
    mocker.patch("services.execution.handlers.roku.is_roku_device", return_value=False)
    mocker.patch("services.execution.handlers.android_tv.is_android_tv", return_value=False)
    mocker.patch("services.execution.handlers.samsung.is_samsung_tv", return_value=False)

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

def test_entity_search_by_query(mocker):
    mocker.patch("services.execution.handlers.media.ha_client.get_states", return_value=[
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
    mocker.patch("services.execution.handlers.media.ha_client.get_states", return_value=[
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

def test_announce_with_device_name(mocker):
    """Test announce resolves device_name and plays media."""
    mocker.patch("services.execution.handlers.media.ha_client.get_states", return_value=[
        {"entity_id": "media_player.office_tv", "state": "on", "attributes": {"friendly_name": "Office TV", "device_class": "tv", "app_id": "com.google.android.tvlauncher"}},
    ])
    mocker.patch("services.execution.handlers.media.ha_client.get_config", return_value={"components": ["cast.media_player", "media_player"]})
    mocker.patch("services.execution.handlers.media.ha_client.call_service", return_value={"ok": True})
    mocker.patch("services.execution.handlers.media.ha_client.get_logbook", return_value=[{"state": "playing", "message": "playing"}])

    # Mock TTS to return empty bytes (will trigger fallback but still test resolution)
    mocker.patch("services.execution.tts.text_to_speech", return_value=b"")

    resp = client.post("/execute/announce",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "device_name": "Office TV",
            "message": "Test announcement",
            "tts_engine": "kokoro",
            "volume": 0.5
        }
    )
    assert resp.status_code == 200


# ─── STT transcribe_workspace (audiobook verification) ────────────────────────

def test_transcribe_workspace_missing_file_path():
    resp = client.post(
        "/execute/stt/transcribe_workspace",
        headers={"X-Internal-Secret": "test-secret"},
        json={"workspace_id": "Test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "FAILURE"
    assert "file_path" in data["message"]


def test_transcribe_workspace_missing_audio_file(mocker):
    mocker.patch(
        "services.execution.handlers.workspace._resolve_workspace_info",
        return_value=("/tmp/nonexistent_ws_root", {}),
    )
    resp = client.post(
        "/execute/stt/transcribe_workspace",
        headers={"X-Internal-Secret": "test-secret"},
        json={"file_path": "missing.wav", "workspace_id": "Test"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "FAILURE"
    assert "does not exist" in data["message"]


def test_transcribe_workspace_success(mocker, tmp_path):
    audio_file = tmp_path / "test_chapter.wav"
    audio_file.write_bytes(b"fake-wav-bytes-just-for-path-check")

    mocker.patch(
        "services.execution.handlers.workspace._resolve_workspace_info",
        return_value=(str(tmp_path), {}),
    )
    fake_model = mocker.MagicMock()
    fake_model.transcribe.return_value = {
        "text": "John chapter 3, verse 16. Nineteen ninety-five. Fourteen hundred B.C.",
    }
    mocker.patch("whisper.load_model", return_value=fake_model)

    resp = client.post(
        "/execute/stt/transcribe_workspace",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "file_path": "test_chapter.wav",
            "workspace_id": "Test",
            "model": "base",
            "language": "en",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    transcript = data["detail"]["transcript"]
    assert "John chapter 3, verse 16" in transcript
    assert "Fourteen hundred" in transcript
    fake_model.transcribe.assert_called_once()
