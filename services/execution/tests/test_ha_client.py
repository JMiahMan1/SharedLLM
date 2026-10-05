import asyncio

import pytest

from services.execution.ha_client import resolve_entity_by_name

mock_states_store: list[dict] = []


async def mock_get_states(*args, **kwargs):
    return mock_states_store


def test_resolve_exact_friendly_name_match():
    """Exact friendly name match should win over partial matches."""
    async def run():
        global mock_states_store
        mock_states_store = [
            {"entity_id": "media_player.office_tv", "attributes": {"friendly_name": "Office TV"}},
            {"entity_id": "media_player.office_tv_chrome", "attributes": {"friendly_name": "Office TV Cast"}},
            {"entity_id": "media_player.office_tv_3", "attributes": {"friendly_name": "Office TV 3"}},
        ]

        from services.execution import ha_client
        original = ha_client.get_states
        ha_client.get_states = mock_get_states

        try:
            result = await resolve_entity_by_name("http://ha.local", "token", "Office TV")
            assert result == "media_player.office_tv", f"Expected media_player.office_tv, got {result}"
        finally:
            ha_client.get_states = original

    asyncio.run(run())


def test_resolve_exact_entity_id_match():
    """Exact entity_id base match should win."""
    async def run():
        global mock_states_store
        mock_states_store = [
            {"entity_id": "media_player.office_tv", "attributes": {"friendly_name": "Office Android TV"}},
            {"entity_id": "media_player.office_tv_chrome", "attributes": {"friendly_name": "Office TV"}},
        ]

        from services.execution import ha_client
        original = ha_client.get_states
        ha_client.get_states = mock_get_states

        try:
            result = await resolve_entity_by_name("http://ha.local", "token", "office_tv")
            assert result == "media_player.office_tv", f"Expected media_player.office_tv, got {result}"
        finally:
            ha_client.get_states = original

    asyncio.run(run())


def test_resolve_starts_with_match():
    """Starts-with match should be preferred over contains."""
    async def run():
        global mock_states_store
        mock_states_store = [
            {"entity_id": "media_player.office_tv_chrome", "attributes": {"friendly_name": "Office TV Chrome"}},
            {"entity_id": "media_player.living_room_office_tv", "attributes": {"friendly_name": "Living Room Office TV"}},
        ]

        from services.execution import ha_client
        original = ha_client.get_states
        ha_client.get_states = mock_get_states

        try:
            result = await resolve_entity_by_name("http://ha.local", "token", "Office TV")
            assert result == "media_player.office_tv_chrome", f"Expected media_player.office_tv_chrome, got {result}"
        finally:
            ha_client.get_states = original

    asyncio.run(run())


def test_resolve_no_match():
    """No match should return None."""
    async def run():
        global mock_states_store
        mock_states_store = [
            {"entity_id": "media_player.kitchen_speaker", "attributes": {"friendly_name": "Kitchen Speaker"}},
        ]

        from services.execution import ha_client
        original = ha_client.get_states
        ha_client.get_states = mock_get_states

        try:
            result = await resolve_entity_by_name("http://ha.local", "token", "Office TV")
            assert result is None, f"Expected None, got {result}"
        finally:
            ha_client.get_states = original

    asyncio.run(run())

def test_resolve_empty_input():
    """Empty device name should return None."""
    async def run():
        result = await resolve_entity_by_name("http://ha.local", "token", "")
        assert result is None

        result = await resolve_entity_by_name("", "token", "Office TV")
        assert result is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "device_name,media_type,states,expected",
    [
        pytest.param(
            "Kitchen Speaker",
            None,
            [
                {"entity_id": "media_player.kitchen_speaker", "attributes": {"friendly_name": "Kitchen Speaker", "device_class": "speaker"}},
                {"entity_id": "media_player.living_room_tv", "attributes": {"friendly_name": "Living Room TV", "device_class": "tv"}},
            ],
            "media_player.kitchen_speaker",
            id="exact-name-beats-tv-device-class-bonus",
        ),
        pytest.param(
            "Kitchen Speaker",
            "music",
            [
                {"entity_id": "media_player.kitchen_speaker", "attributes": {"friendly_name": "Kitchen Speaker", "device_class": "speaker"}},
                {"entity_id": "media_player.office_display", "attributes": {"friendly_name": "Office Display", "active_queue": True, "mass_player_type": "player"}},
            ],
            "media_player.kitchen_speaker",
            id="exact-name-beats-music-assistant-queue-bonus",
        ),
        pytest.param(
            "Bedroom TV",
            "video",
            [
                {"entity_id": "media_player.bedroom_tv", "attributes": {"friendly_name": "Bedroom TV"}},
                {"entity_id": "media_player.living_room_tv", "attributes": {"friendly_name": "Living Room TV", "device_class": "tv", "app_name": "Default Media Receiver"}},
            ],
            "media_player.bedroom_tv",
            id="exact-name-beats-video-cast-bonus",
        ),
        pytest.param(
            "Speaker",
            "music",
            [
                {"entity_id": "media_player.speaker_a", "attributes": {"friendly_name": "Speaker"}},
                {"entity_id": "media_player.speaker_b", "attributes": {"friendly_name": "Speaker", "active_queue": True}},
            ],
            "media_player.speaker_b",
            id="bonus-breaks-tie-between-equal-name-scores",
        ),
        pytest.param(
            "Porch",
            "music",
            [
                {"entity_id": "media_player.office_display", "attributes": {"friendly_name": "Office Display", "active_queue": True}},
            ],
            "media_player.office_display",
            id="capable-entity-still-returned-without-any-name-match",
        ),
    ],
)
def test_resolve_name_match_dominates_capability_bonuses(device_name, media_type, states, expected):
    """Capability bonuses must never outrank a name match; they only break ties."""
    async def run():
        global mock_states_store
        mock_states_store = states

        from services.execution import ha_client
        original = ha_client.get_states
        ha_client.get_states = mock_get_states

        try:
            result = await resolve_entity_by_name(
                "http://ha.local", "token", device_name, media_type=media_type,
            )
            assert result == expected, f"Expected {expected}, got {result}"
        finally:
            ha_client.get_states = original

    asyncio.run(run())
