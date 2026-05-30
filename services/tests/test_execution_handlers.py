# services/tests/test_execution_handlers.py
import os
import pytest
from unittest.mock import AsyncMock, Mock, patch
from typing import Any, cast

os.environ["DEVICE_REGISTRY_PATH"] = ":memory:"

from services.execution.schemas import (
    UserContext, LightControlRequest, MediaTransportRequest,
    TVCastRequest, TalkRequest, VolumeInventoryRequest
)
from services.execution.handlers import light, media, climate, security, talk, volumes
from services.execution.handlers.security import SecurityRequest
from services.execution.personal_data import resolve_personal_data_provider

@pytest.fixture
def user_ctx():
    return {"user": "test_user", "ha_url": "http://ha", "ha_token": "test_tok"}

@pytest.mark.asyncio
async def test_light_handler_success(user_ctx):
    with patch("services.execution.ha_client.call_service", new_callable=AsyncMock) as mock_call, \
         patch("services.execution.ha_client.call_service", mock_call), \
         patch("services.execution.ha_client.get_state", new_callable=AsyncMock) as mock_get_state, \
         patch("services.execution.ha_client.get_state", mock_get_state):
        mock_get_state.return_value = {"state": "off"}
        mock_call.return_value = {"ok": True}
        
        req = LightControlRequest(user_context=user_ctx, entity_id="light.test", action="turn_on", brightness_pct=50)
        res = await light.handle_light(req)
        
        assert res.status == "SUCCESS"
        assert "light.test" in res.message
        mock_call.assert_called_once_with(
            "http://ha", "test_tok", "light", "turn_on", "light.test", {"brightness_pct": 50}
        )

@pytest.mark.asyncio
async def test_hyphenated_entity_resolution(user_ctx):
    """Verify that 'piano-lamp' is correctly sanitized to 'light.piano_lamp'."""
    with patch("services.execution.ha_client.call_service", new_callable=AsyncMock) as mock_call, \
         patch("services.execution.ha_client.call_service", mock_call), \
         patch("services.execution.ha_client.get_state", new_callable=AsyncMock) as mock_get_state, \
         patch("services.execution.ha_client.get_state", mock_get_state):
        mock_get_state.return_value = {"state": "off"}
        mock_call.return_value = {"ok": True}
        
        # Test 1: Bare hyphenated name
        req = LightControlRequest(user_context=user_ctx, entity_id="piano-lamp", action="turn_on")
        res = await light.handle_light(req)
        assert res.status == "SUCCESS"
        mock_call.assert_called_with(
            "http://ha", "test_tok", "light", "turn_on", "light.piano_lamp", None
        )

        # Test 2: Name with apostrophe
        req2 = LightControlRequest(user_context=user_ctx, entity_id="Jeremiah's Lamp", action="turn_on")
        res2 = await light.handle_light(req2)
        assert res2.status == "SUCCESS"
        mock_call.assert_called_with(
            "http://ha", "test_tok", "light", "turn_on", "light.jeremiah_s_lamp", None
        )
@pytest.mark.asyncio
async def test_security_status_check(user_ctx):
    with patch("services.execution.ha_client.get_state", new_callable=AsyncMock) as mock_get_state, \
         patch("services.execution.ha_client.get_state", mock_get_state):
        mock_get_state.return_value = {"state": "open"}
        
        req = SecurityRequest(user_context=user_ctx, entity_id="cover.garage_door", action="status")
        res = await security.handle_security(req)
        
        assert res.status == "SUCCESS"
        assert "open" in res.message
        mock_get_state.assert_called_once_with("http://ha", "test_tok", "cover.garage_door")

@pytest.mark.asyncio
async def test_media_transport_volume(user_ctx):
    with patch("services.execution.ha_client.call_service", new_callable=AsyncMock) as mock_call, \
         patch("services.execution.ha_client.call_service", mock_call), \
         patch("services.execution.ha_client.get_state", new_callable=AsyncMock) as mock_get_state, \
         patch("services.execution.ha_client.get_state", mock_get_state):
        mock_get_state.return_value = {"state": "off"}
        mock_call.return_value = {"ok": True}
        
        req = MediaTransportRequest(user_context=user_ctx, entity_id="media_player.tv", command="volume_up", volume_level=0.5)
        res = await media.handle_media_transport(req)
        
        assert res.status == "SUCCESS"
        # Verify it translated volume_up + level to volume_set
        args = mock_call.call_args[0]
        assert args[3] == "volume_set"
        assert args[5] == {"volume_level": 0.5}

@pytest.mark.asyncio
async def test_climate_handler(user_ctx):
    with patch("services.execution.ha_client.call_service", new_callable=AsyncMock) as mock_call, \
         patch("services.execution.ha_client.call_service", mock_call), \
         patch("services.execution.ha_client.authorize_action", return_value=True) as mock_auth, \
         patch("services.execution.ha_client.authorize_action", mock_auth):
        mock_call.return_value = {"ok": True}
        
        # Use the handler's own ClimateRequest which imports UserContext from execution schemas
        req = climate.ClimateRequest(user_context=user_ctx, entity_id="climate.nest", temperature=72.5)
        res = await climate.handle_climate(req)
        
        assert res.status == "SUCCESS"
        assert "72.5" in res.message
        mock_call.assert_called_once_with(
            "http://ha", "test_tok", "climate", "set_temperature", "climate.nest", {"temperature": 72.5}
        )

@pytest.mark.asyncio
async def test_tv_cast_macro(user_ctx):
    with patch("services.execution.ha_client.get_state", new_callable=AsyncMock) as mock_get_state, \
         patch("services.execution.ha_client.get_state", mock_get_state), \
         patch("services.execution.ha_client.call_service", new_callable=AsyncMock) as mock_call, \
         patch("services.execution.ha_client.call_service", mock_call), \
         patch("services.execution.handlers.video.download_video_progressive", new_callable=AsyncMock) as mock_download, \
         patch("config.EXECUTION_EXTERNAL_HOST", "192.168.2.205"), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        
        mock_download.return_value = ("mock_media_id", "mock_title")
        async def get_state_side_effect(url, token, entity_id):
            if "remote." in entity_id:
                return None
            return {"state": "off"}
        mock_get_state.side_effect = get_state_side_effect
        mock_call.return_value = {"ok": True}
        
        req = TVCastRequest(
            user_context=user_ctx, 
            media_player_entity_id="media_player.generic_tv",
            media_content_id="https://youtube.com/watch?v=mockvideo",
            media_content_type="url",
            power_on_wait_ms=100
        )
        res = await media.handle_tv_cast(req)
        
        assert res.status == "SUCCESS"
        assert mock_call.call_count == 3
        assert mock_call.call_args_list[0][0][3] == "turn_on"
        assert mock_call.call_args_list[1][0][3] == "media_stop"
        assert mock_call.call_args_list[2][0][3] == "play_media"


def test_personal_data_provider_resolves_from_user_context():
    provider = resolve_personal_data_provider(
        UserContext(
            user="default",
            nextcloud_url="https://cloud.example.com",
            nextcloud_user="default",
            nextcloud_pass="secret",
        )
    )

    assert provider is not None
    assert provider.kind == "nextcloud"
    assert provider.base_url == "https://cloud.example.com"
    assert provider.username == "default"


@pytest.mark.asyncio
async def test_talk_send_message_uses_provider_request():
    provider = Mock()
    provider.request.return_value = (
        True,
        {
            "id": 9,
            "token": "room-alpha",
            "actorType": "users",
            "actorId": "default",
            "actorDisplayName": "Default",
            "messageType": "comment",
            "message": "hello world",
            "isReplyable": True,
        },
        "",
    )

    with patch("services.execution.handlers.talk.resolve_personal_data_provider", return_value=provider):
        result = await talk.handle_talk(
            TalkRequest(
                user_context={"user": "default"},
                action="send",
                token="room-alpha",
                message="hello world",
            )
        )

    assert result.status == "SUCCESS"
    assert result.service == "talk_send"
    provider.request.assert_called_once()


@pytest.mark.asyncio
async def test_talk_send_voice_uploads_and_shares():
    provider = Mock()
    provider.sanitize_filename.return_value = "voice.webm"
    provider.upload_file.return_value = Mock(status_code=201)
    provider.request.return_value = (True, {"id": 44}, "")

    with patch("services.execution.handlers.talk.resolve_personal_data_provider", return_value=provider):
        result = await talk.handle_talk(
            TalkRequest(
                user_context={"user": "default"},
                action="send_voice",
                token="room-alpha",
                audio_base64="data:audio/webm;base64,bW9jay1hdWRpbw==",
                mime_type="audio/webm",
                caption="Voice update",
            )
        )

    assert result.status == "SUCCESS"
    assert result.service == "talk_send_voice"
    provider.ensure_directory.assert_called_once()
    provider.upload_file.assert_called_once()
    provider.request.assert_called_once()


@pytest.mark.asyncio
async def test_volume_inventory_requires_admin():
    result = await volumes.handle_volumes(
        VolumeInventoryRequest(user_context=cast(Any, {"user": "default", "is_admin": False}))
    )

    assert result["status"] == "FAILURE"
    assert result["detail"]["error"] == "insufficient_permissions"


@pytest.mark.asyncio
async def test_volume_inventory_merges_manifest_and_docker(monkeypatch):
    class FakeVolume:
        def __init__(self, name, mountpoint="/var/lib/docker/volumes/demo/_data"):
            self.name = name
            self.attrs = {
                "Mountpoint": mountpoint,
                "CreatedAt": "2026-05-06T00:00:00Z",
                "Labels": {"com.docker.compose.volume": name},
            }

    class FakeClient:
        def df(self):
            return {
                "Volumes": [
                    {"Name": "sharedllm_identity_db", "UsageData": {"Size": 4096, "RefCount": 1}},
                    {"Name": "orphaned_volume", "UsageData": {"Size": 2048, "RefCount": 0}},
                ]
            }

        class volumes:
            @staticmethod
            def list():
                return [FakeVolume("sharedllm_identity_db"), FakeVolume("orphaned_volume")]

    monkeypatch.setattr(volumes, "_get_docker_client", lambda: FakeClient())
    monkeypatch.setattr(
        volumes,
        "_load_manifest",
        lambda: {
            "volumes": [
                {
                    "name": "sharedllm_identity_db",
                    "service": "identity",
                    "mount_path": "/data",
                    "category": "database",
                    "criticality": "critical",
                    "rebuildable": False,
                    "backup_policy": "daily",
                    "notes": "Encrypted identity state.",
                }
            ]
        },
    )

    result = await volumes.handle_volumes(
        VolumeInventoryRequest(user_context=cast(Any, {"user": "admin", "is_admin": True}))
    )

    assert result["status"] == "SUCCESS"
    assert result["detail"]["tracked_volumes"] == 1
    assert result["detail"]["unmanaged_volumes"] == 1
    tracked = next(item for item in result["detail"]["volumes"] if item["name"] == "sharedllm_identity_db")
    unmanaged = next(item for item in result["detail"]["volumes"] if item["name"] == "orphaned_volume")
    assert tracked["size_bytes"] == 4096
    assert tracked["backup_example"].startswith("docker run --rm")
    assert unmanaged["category"] == "untracked"
