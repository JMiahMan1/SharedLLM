# services/tests/test_execution_handlers.py
import pytest
import asyncio
from unittest.mock import AsyncMock, Mock, patch
from services.execution.handlers import light, media, climate, security, talk
from services.execution.personal_data import resolve_personal_data_provider
from services.execution.schemas import (
    LightControlRequest, MediaPlayRequest, MediaTransportRequest,
    TVCastRequest, TalkRequest, UserContext
)

@pytest.fixture
def user_ctx():
    return UserContext(user="test_user", ha_url="http://ha", ha_token="test_tok")

@pytest.mark.asyncio
async def test_light_handler_success(user_ctx):
    with patch("services.execution.ha_client.call_service", new_callable=AsyncMock) as mock_call:
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
    with patch("services.execution.ha_client.call_service", new_callable=AsyncMock) as mock_call:
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
    with patch("services.execution.ha_client.get_state", new_callable=AsyncMock) as mock_get_state:
        mock_get_state.return_value = {"state": "open"}
        
        req = security.SecurityRequest(user_context=user_ctx, entity_id="cover.garage_door", action="status")
        res = await security.handle_security(req)
        
        assert res.status == "SUCCESS"
        assert "open" in res.message
        mock_get_state.assert_called_once_with("http://ha", "test_tok", "cover.garage_door")

@pytest.mark.asyncio
async def test_media_transport_volume(user_ctx):
    with patch("services.execution.ha_client.call_service", new_callable=AsyncMock) as mock_call:
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
    with patch("services.execution.ha_client.call_service", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = {"ok": True}
        
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
         patch("services.execution.ha_client.call_service", new_callable=AsyncMock) as mock_call, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        
        mock_get_state.return_value = {"state": "off"}
        mock_call.return_value = {"ok": True}
        
        req = TVCastRequest(
            user_context=user_ctx, 
            media_player_entity_id="media_player.roku",
            media_content_id="http://video",
            media_content_type="url",
            power_on_wait_ms=100
        )
        res = await media.handle_tv_cast(req)
        
        assert res.status == "SUCCESS"
        # Should have called turn_on AND play_media
        assert mock_call.call_count == 2
        assert mock_call.call_args_list[0][0][3] == "turn_on"
        assert mock_call.call_args_list[1][0][3] == "play_media"


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
                user_context=UserContext(user="default"),
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
                user_context=UserContext(user="default"),
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
