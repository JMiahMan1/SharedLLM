# services/execution/tests/test_media_playback_service.py
import os

import pytest

os.environ["DEVICE_REGISTRY_PATH"] = ":memory:"

from services.execution.media_playback_service import MediaPlaybackService
from services.execution.schemas import MediaPlayRequest, MediaStateSyncRequest, MediaStatusRequest, MediaTransportRequest, UserContext

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
    assert active["entity_id"] == "web_player"
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
    assert play_res.detail is not None
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
    assert status_res.detail is not None
    assert status_res.detail["active"]["state"] == "paused"


@pytest.mark.asyncio
async def test_resolve_stream(mocker):
    # Mock search_youtube and download_video_progressive
    mocker.patch("services.execution.handlers.video.search_youtube", return_value="https://youtube.com/watch?v=123")
    mocker.patch("services.execution.handlers.video.download_video_progressive", return_value=("vid-123", "Resolved Title"))

    # Temporarily override config value
    import services.config
    mocker.patch.object(services.config, "EXECUTION_EXTERNAL_HOST", "192.168.2.205")

    from services.execution.main import execute_media_resolve_stream
    from services.execution.schemas import ResolveStreamRequest

    req = ResolveStreamRequest(
        user_context=mock_context,
        query="Yesterday"
    )
    res = await execute_media_resolve_stream(req)
    assert res.status == "SUCCESS"
    assert res.detail is not None
    assert res.detail["stream_url"] == "http://192.168.2.205:8888/media/vid-123"

