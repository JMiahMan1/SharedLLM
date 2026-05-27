"""Integration tests for media_status handler - validates HA media player state parsing."""
import pytest
from unittest.mock import AsyncMock, patch

from services.execution.handlers.media_status import handle_media_status
from services.execution.schemas import MediaStatusRequest, UserContext


def _make_req(**overrides):
    ctx = UserContext(user="testuser", ha_url="http://localhost", ha_token="test")
    area = overrides.pop("area", None)
    entity_id = overrides.pop("entity_id", None)
    return MediaStatusRequest(user_context=ctx, area=area, entity_id=entity_id)


def _make_state(entity_id, state, attrs=None):
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": attrs or {
            "friendly_name": entity_id.split(".")[-1].replace("_", " ").title(),
            "volume_level": 0.5,
            "is_volume_muted": False,
        },
    }


def _make_areas(*entities_with_areas):
    return {eid: area for eid, area in entities_with_areas}


@pytest.mark.asyncio
async def test_media_status_returns_active_player():
    """Test that the first active player is returned in detail['active']."""
    states = [
        _make_state("media_player.tv", "playing"),
        _make_state("media_player.speaker", "paused"),
    ]
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value={})),
    ):
        result = await handle_media_status(_make_req())

    assert result.status == "SUCCESS"
    assert result.service == "media_status"
    active = result.detail.get("active") if result.detail else None
    assert active is not None
    assert isinstance(active, dict)
    assert active["entity_id"] == "media_player.tv"
    assert active["state"] in ("playing", "paused")


@pytest.mark.asyncio
async def test_media_status_returns_available_players():
    """Test that idle/standby/off players are returned in detail['available']."""
    states = [
        _make_state("media_player.idle", "idle"),
        _make_state("media_player.off", "off"),
        _make_state("media_player.standby", "standby"),
    ]
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value={})),
    ):
        result = await handle_media_status(_make_req())

    assert result.status == "SUCCESS"
    available = result.detail.get("available") if result.detail else None
    assert isinstance(available, list)
    assert len(available) == 3
    ids = [p["entity_id"] for p in available]
    assert "media_player.idle" in ids
    assert "media_player.off" in ids
    assert "media_player.standby" in ids


@pytest.mark.asyncio
async def test_media_status_non_media_entities_filtered():
    """Test that non-media_player entities are filtered out."""
    states = [
        _make_state("media_player.tv", "playing"),
        _make_state("light.bedroom", "on", {"friendly_name": "Bedroom Light"}),
        _make_state("switch.outlet", "on", {"friendly_name": "Outlet"}),
    ]
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value={})),
    ):
        result = await handle_media_status(_make_req())

    all_players = (result.detail or {}).get("all_players") or []
    assert len(all_players) == 1
    assert all_players[0]["entity_id"] == "media_player.tv"


@pytest.mark.asyncio
async def test_media_status_volume_rounding():
    """Test that volume_level is properly rounded to 2 decimal places."""
    states = [_make_state("media_player.tv", "playing", {"volume_level": 0.123456789})]
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value={})),
    ):
        result = await handle_media_status(_make_req())

    active = result.detail["active"] if result.detail else None
    assert active is not None
    assert active["volume_level"] is not None
    assert round(active["volume_level"], 2) == active["volume_level"]


@pytest.mark.asyncio
async def test_media_status_null_volume():
    """Test that null volume_level is handled gracefully."""
    states = [_make_state("media_player.tv", "playing", {"volume_level": None})]
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value={})),
    ):
        result = await handle_media_status(_make_req())

    active = result.detail["active"] if result.detail else None
    assert active is not None
    assert active["volume_level"] is None


@pytest.mark.asyncio
async def test_media_status_area_filter():
    """Test that area filtering works correctly."""
    states = [
        _make_state("media_player.living_tv", "playing"),
        _make_state("media_player.bedroom_tv", "playing"),
    ]
    area_map = _make_areas(
        ("media_player.living_tv", "Living Room"),
        ("media_player.bedroom_tv", "Bedroom"),
    )
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value=area_map)),
    ):
        result = await handle_media_status(_make_req(area="Living Room"))

    all_players = result.detail.get("all_players") if result.detail else None
    assert all_players is not None
    ids = [p["entity_id"] for p in all_players]
    assert "media_player.living_tv" in ids
    assert "media_player.bedroom_tv" not in ids


@pytest.mark.asyncio
async def test_media_status_entity_filter():
    """Test that entity_id filtering works correctly."""
    states = [
        _make_state("media_player.tv_living", "playing"),
        _make_state("media_player.tv_bedroom", "playing"),
    ]
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value={})),
    ):
        result = await handle_media_status(_make_req(entity_id="tv_living"))

    all_players = result.detail.get("all_players") if result.detail else None
    assert all_players is not None
    ids = [p["entity_id"] for p in all_players]
    assert "media_player.tv_living" in ids
    assert "media_player.tv_bedroom" not in ids


@pytest.mark.asyncio
async def test_media_status_no_active_players():
    """Test when no media players are active."""
    states = [_make_state("media_player.speaker", "idle")]
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value={})),
    ):
        result = await handle_media_status(_make_req())

    assert result.status == "SUCCESS"
    active = result.detail.get("active") if result.detail else None
    assert active is None
    available = result.detail.get("available") if result.detail else None
    assert isinstance(available, list)
    assert len(available) == 1
    assert "No media players are currently active" in result.message


@pytest.mark.asyncio
async def test_media_status_empty_states_returns_failure():
    """Test when HA returns no states."""
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=[])),
    ):
        result = await handle_media_status(_make_req())

    assert result.status == "FAILURE"
    assert "Could not retrieve HA states" in result.message


@pytest.mark.asyncio
async def test_media_status_message_includes_player_info():
    """Test that the formatted message includes player info."""
    states = [
        _make_state(
            "media_player.tv",
            "playing",
            {
                "friendly_name": "Test TV",
                "volume_level": 0.75,
                "media_title": "Song Title",
                "media_artist": "Artist Name",
            },
        )
    ]
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value={})),
    ):
        result = await handle_media_status(_make_req())

    assert "Test TV" in result.message
    assert "Song Title" in result.message
    assert "75%" in result.message


@pytest.mark.asyncio
async def test_media_status_muted_volume_captured():
    """Test that is_volume_muted is captured."""
    states = [
        _make_state(
            "media_player.speaker",
            "playing",
            {"volume_level": 0.5, "is_volume_muted": True},
        )
    ]
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value={})),
    ):
        result = await handle_media_status(_make_req())

    active = result.detail["active"] if result.detail else None
    assert active is not None
    assert active["is_volume_muted"] is True


@pytest.mark.asyncio
async def test_media_status_buffering_is_active():
    """Test that buffering state is treated as active."""
    states = [_make_state("media_player.player", "buffering")]
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value={})),
    ):
        result = await handle_media_status(_make_req())

    active = result.detail["active"] if result.detail else None
    assert active is not None
    assert active["state"] == "buffering"


@pytest.mark.asyncio
async def test_media_status_missing_attributes_handled():
    """Test that missing attributes don't crash."""
    states = [{"entity_id": "media_player.minimal", "state": "playing"}]
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value={})),
    ):
        result = await handle_media_status(_make_req())

    assert result.status == "SUCCESS"
    active = result.detail["active"] if result.detail else None
    assert active is not None
    assert active["entity_id"] == "media_player.minimal"
    assert active["friendly_name"] == "media_player.minimal"


@pytest.mark.asyncio
async def test_media_status_all_players_combined():
    """Test that all_players combines active and available."""
    states = [
        _make_state("media_player.playing", "playing"),
        _make_state("media_player.idle", "idle"),
    ]
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value={})),
    ):
        result = await handle_media_status(_make_req())

    all_players = result.detail.get("all_players") if result.detail else None
    assert all_players is not None
    ids = [p["entity_id"] for p in all_players]
    assert "media_player.playing" in ids
    assert "media_player.idle" in ids
    assert len(all_players) == 2


@pytest.mark.asyncio
async def test_media_status_media_attrs_captured():
    """Test that media attributes are captured correctly."""
    states = [
        _make_state(
            "media_player.tv",
            "playing",
            {
                "media_title": "My Song",
                "media_artist": "My Artist",
                "media_album_name": "My Album",
                "source": "Spotify",
            },
        )
    ]
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value={})),
    ):
        result = await handle_media_status(_make_req())

    active = result.detail["active"] if result.detail else None
    assert active is not None
    assert active["media_title"] == "My Song"
    assert active["media_artist"] == "My Artist"
    assert active["media_album"] == "My Album"
    assert active["source"] == "Spotify"


@pytest.mark.asyncio
async def test_media_status_area_filter_no_matches():
    """Test area filter when no players match."""
    states = [_make_state("media_player.bedroom_tv", "playing")]
    area_map = _make_areas(("media_player.bedroom_tv", "Bedroom"))
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value=area_map)),
    ):
        result = await handle_media_status(_make_req(area="Kitchen"))

    all_players = result.detail.get("all_players") if result.detail else None
    assert all_players is not None
    assert len(all_players) == 0


@pytest.mark.asyncio
async def test_media_status_empty_area_map_for_filter():
    """Test area filter with empty area map."""
    states = [_make_state("media_player.tv", "playing")]
    with (
        patch("handlers.media_status.ha_client.get_states", new=AsyncMock(return_value=states)),
        patch("handlers.media_status.ha_client.get_areas", new=AsyncMock(return_value={})),
    ):
        result = await handle_media_status(_make_req(area="Living Room"))

    all_players = result.detail.get("all_players") if result.detail else None
    assert all_players is not None
    assert len(all_players) == 0
