# services/execution/tests/test_media_playback_service.py
import pytest
import os
os.environ["DEVICE_REGISTRY_PATH"] = ":memory:"

from services.execution.schemas import (
    UserContext, MediaPlayRequest, MediaTransportRequest,
    MediaStatusRequest, MediaStateSyncRequest
)
from services.execution.media_playback_service import MediaPlaybackService
from services.execution import media_playback_registry as registry

mock_context = UserContext(
    user="test_user",
    ha_url="http://ha.local",
    ha_token="mock-token"
)

mock_ha_states = [
    {
        "entity_id": "media_player.kitchen",
        "state": "idle",
        "attributes": {
            "friendly_name": "Kitchen Speaker",
            "volume_level": 0.5,
            "is_volume_muted": False
        }
    }
]

@pytest.mark.asyncio
async def test_sync_local_and_status(mocker):
    mocker.patch("services.execution.ha_client.get_states", return_value=mock_ha_states)
    try:
        mocker.patch("ha_client.get_states", return_value=mock_ha_states)
    except ModuleNotFoundError:
        pass
    
    # Sync initial local state
    sync_req = MediaStateSyncRequest(
        user_context=mock_context,
        entity_id="local",
        state="playing",
        media_type="music",
        query="The Beatles",
        media_content_id="track_123",
        position=10.5,
        duration=180.0,
        volume_level=0.8,
        is_volume_muted=False,
        media_title="Yesterday",
        media_artist="The Beatles",
        media_album="Help!"
    )
    
    res = await MediaPlaybackService.sync_local(sync_req)
    assert res.status == "SUCCESS"
    
    # Query status
    status_req = MediaStatusRequest(user_context=mock_context)
    status_res = await MediaPlaybackService.status(status_req)
    assert status_res.status == "SUCCESS"
    
    active = status_res.detail.get("active")
    assert active is not None
    assert active["entity_id"] == "local_player"
    assert active["media_title"] == "Yesterday"
    assert active["media_artist"] == "The Beatles"
    assert active["position"] == 10.5
    assert active["volume_level"] == 0.8
    assert active["state"] == "playing"

@pytest.mark.asyncio
async def test_play_local_and_transport(mocker):
    mocker.patch("services.execution.ha_client.get_states", return_value=mock_ha_states)
    try:
        mocker.patch("ha_client.get_states", return_value=mock_ha_states)
    except ModuleNotFoundError:
        pass
    
    # Play locally
    play_req = MediaPlayRequest(
        user_context=mock_context,
        entity_id="local",
        query="Yesterday",
        media_type="music",
        volume=0.5
    )
    
    play_res = await MediaPlaybackService.play(play_req)
    assert play_res.status == "SUCCESS"
    assert play_res.detail["target"] == "local"
    assert play_res.detail["media_title"] == "Yesterday"
    
    # Run transport pause command
    trans_req = MediaTransportRequest(
        user_context=mock_context,
        entity_id="local",
        command="pause"
    )
    
    trans_res = await MediaPlaybackService.transport(trans_req)
    assert trans_res.status == "SUCCESS"
    
    # Verify status reflects pause
    status_req = MediaStatusRequest(user_context=mock_context)
    status_res = await MediaPlaybackService.status(status_req)
    assert status_res.detail["active"]["state"] == "paused"
