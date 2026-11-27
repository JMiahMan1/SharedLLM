
from fastapi.testclient import TestClient

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
    # BUG-25 (P2-T16): a failed/empty MASS search returns "No match" instead of
    # playing a random track, so this test supplies a real search match.
    def fake_call_service(*a, **kw):
        if kw.get("service") == "search" or (len(a) >= 4 and a[3] == "search"):
            return {"ok": True, "service_response": {"service_response": {"tracks": [{"uri": "library://track/1", "name": "Beatles Track"}]}}}
        return {"ok": True, "status_code": 200}

    mocker.patch("services.execution.handlers.media.ha_client.call_service", side_effect=fake_call_service)

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


def test_media_play_no_match_is_failure(mocker):
    # BUG-25 (P2-T16): no search match → FAILURE "No match", nothing played.
    calls = []

    def fake_call_service(*a, **kw):
        calls.append(a[3] if len(a) > 3 else kw.get("service"))
        return {"ok": True, "service_response": {"service_response": {"tracks": []}}}

    mocker.patch("services.execution.handlers.media.ha_client.call_service", side_effect=fake_call_service)

    resp = client.post("/execute/media/play",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "entity_id": "media_player.kitchen",
            "query": "zzz no such song zzz"
        }
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILURE"
    assert "No match" in body["message"]
    assert "play_media" not in calls
    assert "get_library" not in calls

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
        {"entity_id": "media_player.study_tv", "state": "idle", "attributes": {"friendly_name": "Study TV", "device_class": "tv"}},
        {"entity_id": "media_player.study_tv_cast", "state": "off", "attributes": {"friendly_name": "Study TV Cast", "device_class": "speaker"}},
        {"entity_id": "light.study_desk", "state": "on", "attributes": {"friendly_name": "Study Desk Light", "device_class": "light"}},
    ])

    resp = client.post("/execute/entity/search",
        headers={"X-Internal-Secret": "test-secret"},
        json={
            "user_context": valid_context,
            "query": "study tv",
            "domain": "media_player"
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    entities = data["detail"]["entities"]
    assert len(entities) >= 1
    assert entities[0]["entity_id"].startswith("media_player.")
    assert "study" in entities[0]["entity_id"].lower() or "tv" in entities[0]["entity_id"].lower()

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
        {"entity_id": "media_player.study_tv", "state": "on", "attributes": {"friendly_name": "Study TV", "device_class": "tv", "app_id": "com.google.android.tvlauncher"}},
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
            "device_name": "Study TV",
            "message": "Test announcement",
            "tts_engine": "kokoro",
            "volume": 0.5
        }
    )
    assert resp.status_code == 200


# STT transcribe_workspace (audiobook verification)
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
    fake_whisper = mocker.MagicMock()
    fake_model = mocker.MagicMock()
    fake_model.transcribe.return_value = {
        "text": "John chapter 3, verse 16. Nineteen ninety-five. Fourteen hundred B.C.",
    }
    fake_whisper.load_model.return_value = fake_model
    mocker.patch.dict("sys.modules", {"whisper": fake_whisper})

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


def test_media_transport_by_device_name_without_an_own_login(mocker):
    """A voice request names the TV and carries no hub login of its own (2026-11-24: "volume down on
    the Office TV" answered "I couldn't find a speaker in Office TV" while the same command by entity id
    worked). The route adds the hub login, resolves the name, and checks access to what it found."""
    states = [
        {"entity_id": "media_player.office_tv_cast", "state": "playing",
         "attributes": {"friendly_name": "Office TV Cast", "volume_level": 0.5}},
        {"entity_id": "light.office_lamp", "state": "on", "attributes": {"friendly_name": "Office lamp"}},
    ]
    seen_logins = []

    async def get_states(url, token):
        seen_logins.append((url, token))
        return states

    mocker.patch("services.execution.handlers.media.ha_client.get_states", side_effect=get_states)
    mocker.patch("services.execution.main.hardware_router.hub_login", return_value=("http://hub.local", "hub-token"))
    checked = []

    async def access(ctx, entity_id):
        checked.append(entity_id)
        return True

    mocker.patch("services.execution.main.verify_entity_access", side_effect=access)
    calls = []

    async def transport(req):
        calls.append((req.entity_id, req.command, req.volume_level))
        from services.execution.schemas import ExecutionResult
        return ExecutionResult(status="SUCCESS", message="ok", service="media_transport")

    mocker.patch("services.execution.main.MediaPlaybackService.transport", side_effect=transport)
    resp = client.post("/execute/media/transport",
        headers={"X-Internal-Secret": "test-secret"},
        json={"user_context": {"user": "household"}, "device_name": "Office TV",
              "command": "volume_set", "volume_level": 0.3})
    assert resp.status_code == 200, resp.text
    assert calls == [("media_player.office_tv_cast", "volume_set", 0.3)]
    assert checked == ["media_player.office_tv_cast"]
    assert seen_logins and seen_logins[0] == ("http://hub.local", "hub-token")


def test_media_transport_by_device_name_is_refused_without_access(mocker):
    states = [{"entity_id": "media_player.office_tv_cast", "state": "playing",
               "attributes": {"friendly_name": "Office TV Cast"}}]
    mocker.patch("services.execution.handlers.media.ha_client.get_states", return_value=states)
    mocker.patch("services.execution.main.hardware_router.hub_login", return_value=("http://hub.local", "hub-token"))
    mocker.patch("services.execution.main.verify_entity_access", return_value=False)
    transport = mocker.patch("services.execution.main.MediaPlaybackService.transport")
    resp = client.post("/execute/media/transport",
        headers={"X-Internal-Secret": "test-secret"},
        json={"user_context": {"user": "kid"}, "device_name": "Office TV", "command": "volume_up"})
    assert resp.status_code == 403
    transport.assert_not_called()
