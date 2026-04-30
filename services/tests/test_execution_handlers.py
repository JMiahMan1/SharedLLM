# services/tests/test_execution_handlers.py
import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from services.execution.handlers import light, media, climate, security
from services.execution.schemas import (
    LightControlRequest, MediaPlayRequest, MediaTransportRequest,
    TVCastRequest, UserContext
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
