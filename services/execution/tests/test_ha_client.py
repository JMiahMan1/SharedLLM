import os
import sys
import asyncio

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ha_client import resolve_entity_by_name

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

        import ha_client
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

        import ha_client
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

        import ha_client
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

        import ha_client
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
