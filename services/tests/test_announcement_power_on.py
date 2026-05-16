"""Tests for announcement power-on flow improvements."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_announcement_powers_on_tv_with_longer_timeout():
    """TVs need 5s wake timeout vs 2s for speakers."""
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
        "attributes": {"friendly_name": "Living Room TV"},
    })
    mock_call_service = AsyncMock(return_value={"ok": True})

    with patch("execution.main.ha_client.get_state", mock_get_state), \
         patch("execution.main.ha_client.call_service", mock_call_service), \
         patch("execution.main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await execute_announce(req)

    # Verify TV got 5s wake timeout
    mock_sleep.assert_any_call(5.0)
    # Verify turn_on was called
    mock_call_service.assert_any_call(
        "http://ha.local:8123", "test-token",
        "media_player", "turn_on", "media_player.living_room_tv", {}
    )


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
        "attributes": {"friendly_name": "Kitchen Speaker"},
    })
    mock_call_service = AsyncMock(return_value={"ok": True})

    with patch("execution.main.ha_client.get_state", mock_get_state), \
         patch("execution.main.ha_client.call_service", mock_call_service), \
         patch("execution.main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await execute_announce(req)

    # Should NOT call turn_on
    for call in mock_call_service.call_args_list:
        assert call.args[2] != "turn_on" or call.args[3] != "media_player.kitchen_speaker"
    # Should NOT sleep for wake timeout
    mock_sleep.assert_not_called()


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
            return {"state": "off", "attributes": {"friendly_name": "Bedroom TV"}}
        return {"state": "unavailable", "attributes": {"friendly_name": "Bedroom TV"}}

    mock_call_service = AsyncMock(return_value={"ok": True})

    with patch("execution.main.ha_client.get_state", mock_get_state), \
         patch("execution.main.ha_client.call_service", mock_call_service), \
         patch("execution.main.asyncio.sleep", new_callable=AsyncMock):
        result = await execute_announce(req)

    assert result.status == "FAILURE"
    assert "could not be powered on" in result.message.lower()
