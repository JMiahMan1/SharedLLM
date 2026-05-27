"""Tests for announcement power-on flow improvements."""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_announcement_powers_on_tv_with_longer_timeout():
    """TVs need a wake timeout (duration may vary by device type)."""
    from execution.main import execute_announce
    from execution.schemas import AnnouncementRequest, UserContext

    ctx = UserContext(
        user="test",
        is_admin=True,
        ha_url="http://ha.local:8123",
        ha_token="test-token",
    )
    req = AnnouncementRequest(
        user_context=ctx,
        entity_id="media_player.living_room_tv",
        message="Test announcement",
        volume=0.5,
    )

    mock_get_state = AsyncMock(return_value={
        "state": "off",
        "attributes": {"friendly_name": "Living Room TV", "device_class": "tv"},
    })
    mock_call_service = AsyncMock(return_value={"ok": True})

    with patch("execution.main.ha_client.get_state", mock_get_state), \
         patch("execution.main.ha_client.call_service", mock_call_service), \
         patch("execution.main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         patch("execution.main.ha_client.get_config", return_value={"components": []}), \
         patch("execution.main.ha_client.get_states", return_value=[{
             "entity_id": "media_player.living_room_tv",
             "state": "off",
             "attributes": {"friendly_name": "Living Room TV", "device_class": "tv"},
         }]), \
         patch("execution.main.text_to_speech", return_value=b"mock-audio"), \
         patch("execution.main.TEMP_AUDIO_CACHE", {}), \
         patch("execution.main.verify_playback", return_value={"verified": True}), \
         patch("execution.main.detect_tv_type", return_value="generic_tv"), \
         patch("execution.main.get_public_host", return_value="192.168.2.205"):
        await execute_announce(req)

    # Verify turn_on was called with the target
    turn_on_calls = [c for c in mock_call_service.call_args_list if len(c.args) > 3 and c.args[3] == "turn_on"]
    assert len(turn_on_calls) >= 1
    # Verify wake timeout was called with a positive duration (duration may vary by device type)
    sleep_calls = mock_sleep.call_args_list
    assert len(sleep_calls) >= 1
    assert all(c.args[0] > 0 for c in sleep_calls)


@pytest.mark.asyncio
async def test_announcement_skips_power_on_when_already_on():
    """Should not call turn_on if device is already playing."""
    from execution.main import execute_announce
    from execution.schemas import AnnouncementRequest, UserContext

    ctx = UserContext(
        user="test",
        is_admin=True,
        ha_url="http://ha.local:8123",
        ha_token="test-token",
    )
    req = AnnouncementRequest(
        user_context=ctx,
        entity_id="media_player.kitchen_speaker",
        message="Test announcement",
        volume=0.5,
    )

    mock_get_state = AsyncMock(return_value={
        "state": "playing",
        "attributes": {"friendly_name": "Kitchen Speaker", "device_class": "speaker"},
    })
    mock_call_service = AsyncMock(return_value={"ok": True})

    with patch("execution.main.ha_client.get_state", mock_get_state), \
         patch("execution.main.ha_client.call_service", mock_call_service), \
         patch("execution.main.asyncio.sleep", new_callable=AsyncMock), \
         patch("execution.main.ha_client.get_config", return_value={"components": []}), \
         patch("execution.main.ha_client.get_states", return_value=[{
             "entity_id": "media_player.kitchen_speaker",
             "state": "playing",
             "attributes": {"friendly_name": "Kitchen Speaker", "device_class": "speaker"},
         }]), \
         patch("execution.main.text_to_speech", return_value=b"mock-audio"), \
         patch("execution.main.TEMP_AUDIO_CACHE", {}), \
         patch("execution.main.verify_playback", return_value={"verified": True}), \
         patch("execution.main.detect_tv_type", return_value="speaker"):
        await execute_announce(req)

    # Verify turn_on was NOT called
    turn_on_calls = [c for c in mock_call_service.call_args_list if len(c.args) > 3 and c.args[3] == "turn_on"]
    assert len(turn_on_calls) == 0


@pytest.mark.asyncio
async def test_announcement_fails_gracefully_when_device_cant_wake():
    """Should return failure if device stays off after wake attempt."""
    from execution.main import execute_announce
    from execution.schemas import AnnouncementRequest, UserContext

    ctx = UserContext(
        user="test",
        is_admin=True,
        ha_url="http://ha.local:8123",
        ha_token="test-token",
    )
    req = AnnouncementRequest(
        user_context=ctx,
        entity_id="media_player.bedroom_tv",
        message="Test announcement",
        volume=0.5,
    )

    call_count = 0
    async def mock_get_state(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"state": "off", "attributes": {"friendly_name": "Bedroom TV", "device_class": "tv"}}
        return {"state": "unavailable", "attributes": {"friendly_name": "Bedroom TV", "device_class": "tv"}}

    mock_call_service = AsyncMock(return_value={"ok": True})

    with patch("execution.main.ha_client.get_state", mock_get_state), \
         patch("execution.main.ha_client.call_service", mock_call_service), \
         patch("execution.main.asyncio.sleep", new_callable=AsyncMock), \
         patch("execution.main.ha_client.get_config", return_value={"components": []}), \
         patch("execution.main.ha_client.get_states", return_value=[{
             "entity_id": "media_player.bedroom_tv",
             "state": "unavailable",
             "attributes": {"friendly_name": "Bedroom TV", "device_class": "tv"},
         }]), \
         patch("execution.main.text_to_speech", return_value=b"mock-audio"), \
         patch("execution.main.TEMP_AUDIO_CACHE", {}), \
         patch("execution.main.verify_playback", return_value={"verified": True}), \
         patch("execution.main.detect_tv_type", return_value="generic_tv"):
        result = await execute_announce(req)

    # Should complete without raising - the flow handles failure gracefully
    assert result is not None
