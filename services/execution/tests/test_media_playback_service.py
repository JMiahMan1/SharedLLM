# services/execution/tests/test_media_playback_service.py
import os

import pytest


import contextlib

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
    with contextlib.suppress(ModuleNotFoundError):
        mocker.patch("ha_client.get_states", return_value=mock_ha_states)

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
    with contextlib.suppress(ModuleNotFoundError):
        mocker.patch("ha_client.get_states", return_value=mock_ha_states)

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
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(res.detail["stream_url"])
    assert parsed.scheme == "http"
    assert parsed.netloc == "192.168.2.205:8888"
    assert parsed.path == "/media/vid-123"
    query = parse_qs(parsed.query)
    assert query.get("user") == ["test_user"]
    from services.shared.media_token import verify

    assert verify(query["mt"][0], "test_user") is True


# --- P2-T15 / BUG-24: registry race, save-after-success, alias unification, None guard ---


@pytest.mark.asyncio
async def test_save_playback_state_upserts_after_stale_read(monkeypatch):
    """BUG-24: a read-then-write race must not fail the save (atomic upsert)."""
    from services.execution import media_playback_registry as registry

    username = "bug24_race"
    real_get = registry.get_playback_state
    assert await registry.save_playback_state(username, {"entity_id": "local", "state": "idle"}) is True

    # Simulate a concurrent writer inserting the row after our existence check:
    # the read reports "missing" even though the row is already there.
    async def stale_read(_username):
        return None

    monkeypatch.setattr(registry, "get_playback_state", stale_read)
    ok = await registry.save_playback_state(username, {"entity_id": "local", "state": "playing"})
    assert ok is True

    row = await real_get(username)
    assert row is not None
    assert row["state"] == "playing"


@pytest.mark.asyncio
async def test_play_hardware_writes_state_only_after_success(mocker):
    """BUG-24: 'playing' must only be persisted after the play handler succeeds."""
    from services.execution import media_playback_registry as registry
    from services.execution.handlers import media as media_handler
    from services.execution.schemas import ExecutionResult

    mocker.patch.object(media_handler, "resolve_entity", return_value="media_player.kitchen")
    handler = mocker.patch.object(
        media_handler,
        "handle_media_play",
        return_value=ExecutionResult(status="FAILURE", message="boom", service="media_play"),
    )

    username = "bug24_hw_fail"
    req = MediaPlayRequest(
        user_context=UserContext(user=username, ha_url="http://ha.local", ha_token="t"),
        entity_id="kitchen",
        query="song",
    )

    res = await MediaPlaybackService.play(req)
    assert res.status == "FAILURE"
    handler.assert_awaited_once()

    row = await registry.get_playback_state(username)
    assert row is None or row.get("state") != "playing"

    # Success path still persists the active target.
    handler.return_value = ExecutionResult(status="SUCCESS", message="ok", service="media_play")
    res2 = await MediaPlaybackService.play(req)
    assert res2.status == "SUCCESS"
    row2 = await registry.get_playback_state(username)
    assert row2 is not None
    assert row2["state"] == "playing"
    assert row2["entity_id"] == "media_player.kitchen"


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["browser", "android"])
async def test_status_treats_all_local_aliases_as_local(mocker, alias):
    """BUG-24: status must recognize the same local aliases as play/transport."""
    from services.execution import media_playback_registry as registry

    mocker.patch("services.execution.ha_client.get_states", return_value=mock_ha_states)

    username = f"bug24_alias_{alias}"
    assert await registry.save_playback_state(
        username, {"entity_id": alias, "state": "playing", "media_title": "AliasSong"}
    ) is True

    req = MediaStatusRequest(
        user_context=UserContext(user=username, ha_url="http://ha.local", ha_token="t")
    )
    res = await MediaPlaybackService.status(req)
    assert res.status == "SUCCESS"
    assert res.detail is not None
    active = res.detail.get("active")
    assert active is not None
    assert active["entity_id"] == "web_player"
    assert active["media_title"] == "AliasSong"


@pytest.mark.asyncio
async def test_status_and_transport_guard_none_entity_id(mocker):
    """BUG-24: a NULL entity_id from the DB must not crash .lower()."""
    from services.execution import media_playback_registry as registry

    mocker.patch("services.execution.ha_client.get_states", return_value=mock_ha_states)

    username = "bug24_none"
    assert await registry.save_playback_state(
        username, {"entity_id": None, "state": "paused", "media_title": "NullRow"}
    ) is True

    ctx = UserContext(user=username, ha_url="http://ha.local", ha_token="t")
    status_res = await MediaPlaybackService.status(MediaStatusRequest(user_context=ctx))
    assert status_res.status == "SUCCESS"

    trans_res = await MediaPlaybackService.transport(
        MediaTransportRequest(user_context=ctx, entity_id="", command="pause")
    )
    assert trans_res.status == "SUCCESS"

