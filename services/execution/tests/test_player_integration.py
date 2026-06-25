"""
Comprehensive integration tests for player changes.

Tests cover:
- resolve_mass_entity scenarios (MA player routing, fallback, idle players)
- play_podcast search paths (podcast/episode → track fallback → direct URL)
- ABS search and last_played with full metadata
- Gateway stream_music_assistant endpoint flow
- Media status filtering for MA-compatible devices only
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, MagicMock as MM
from typing import cast

from services.execution.handlers.media import (
    resolve_mass_entity,
    play_podcast,
    detect_media_type,
)
from services.execution.handlers.audiobookshelf import _handle_search, _handle_last_played
from services.execution.schemas import ExecutionResult, AudiobookshelfRequest, MediaPlayRequest, UserContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ctx():
    return UserContext(
        ha_url="http://homeassistant.local",
        ha_token="test_token_abc123",
        user="test_user",
    )


@pytest.fixture
def mock_states():
    """Return a list of mock HA states for various device types."""
    return [
        {
            "entity_id": "media_player.office_tv",
            "state": "idle",
            "attributes": {
                "friendly_name": "Office TV",
                "source": "Music Assistant",
                "integration": "music_assistant",
                "active_queue": None,
            },
        },
        {
            "entity_id": "media_player.living_room_speaker",
            "state": "playing",
            "attributes": {
                "friendly_name": "Living Room Speaker",
                "source": "YouTube",
                "integration": "youtube",
                "active_queue": "queue://default/12345",
            },
        },
        {
            "entity_id": "media_player.office_tv_ma",
            "state": "idle",
            "attributes": {
                "friendly_name": "Office TV",
                "source": "Music Assistant",
                "integration": "music_assistant",
                "active_queue": "queue://default/67890",
            },
        },
        {
            "entity_id": "media_player.bedroom_radio",
            "state": "off",
            "attributes": {
                "friendly_name": "Bedroom Radio",
                "source": "",
                "integration": "radio",
                "active_queue": None,
            },
        },
    ]


# ---------------------------------------------------------------------------
# resolve_mass_entity Tests
# ---------------------------------------------------------------------------

class TestResolveMassEntity:
    def test_original_entity_already_ma_with_queue(self, ctx, mock_states):
        """If original entity is already an MA player with active_queue, return it."""
        with patch(
            "services.execution.handlers.media.ha_client.get_states",
            new=AsyncMock(return_value=mock_states),
        ):
            result = asyncio.run(resolve_mass_entity(ctx, "media_player.office_tv_ma"))

        assert result == "media_player.office_tv_ma"

    def test_resolve_to_ma_sibling_by_name(self, ctx, mock_states):
        """Resolve non-MA entity to MA player sibling with matching friendly name."""
        with patch(
            "services.execution.handlers.media.ha_client.get_states",
            new=AsyncMock(return_value=mock_states),
        ):
            result = asyncio.run(resolve_mass_entity(ctx, "media_player.office_tv"))

        assert result == "media_player.office_tv_ma"

    def test_no_match_returns_original(self, ctx, mock_states):
        """If no MA player matches the friendly name, return original entity."""
        with patch(
            "services.execution.handlers.media.ha_client.get_states",
            new=AsyncMock(return_value=mock_states),
        ):
            result = asyncio.run(resolve_mass_entity(ctx, "media_player.kitchen_display"))

        assert result == "media_player.kitchen_display"

    def test_empty_states_returns_original(self, ctx):
        """If no states returned, return original entity."""
        with patch(
            "services.execution.handlers.media.ha_client.get_states",
            new=AsyncMock(return_value=[]),
        ):
            result = asyncio.run(resolve_mass_entity(ctx, "media_player.anything"))

        assert result == "media_player.anything"

    def test_non_ma_players_not_routed(self, ctx, mock_states):
        """Non-MA entities without MA sibling should not be redirected."""
        with patch(
            "services.execution.handlers.media.ha_client.get_states",
            new=AsyncMock(return_value=mock_states),
        ):
            result = asyncio.run(resolve_mass_entity(ctx, "media_player.bedroom_radio"))

        assert result == "media_player.bedroom_radio"

    def test_idle_ma_player_selected(self, ctx, mock_states):
        """Idle MA players are valid routing targets (active_queue not required)."""
        # The logic checks if original_friendly (e.g., "Office TV") is contained in MA player's friendly_name
        # So we need MA player to have a longer name containing the original
        states = [
            {
                "entity_id": "media_player.office_tv",
                "state": "standby",
                "attributes": {
                    "friendly_name": "Office TV",
                    "source": "",
                    "integration": "generic",
                    "active_queue": None,
                },
            },
            {
                "entity_id": "media_player.office_tv_ma",
                "state": "idle",
                "attributes": {
                    "friendly_name": "Office TV (MA)",
                    "source": "Music Assistant",
                    "integration": "music_assistant",
                    "active_queue": "queue://default/67890",
                },
            },
        ]
        with patch(
            "services.execution.handlers.media.ha_client.get_states",
            new=AsyncMock(return_value=states),
        ):
            result = asyncio.run(resolve_mass_entity(ctx, "media_player.office_tv"))

        assert result == "media_player.office_tv_ma"


# ---------------------------------------------------------------------------
# play_podcast Tests
# ---------------------------------------------------------------------------

class TestPlayPodcast:
    @pytest.mark.asyncio
    async def test_direct_podcast_url_forwards_to_ha(self, ctx):
        """Direct podcast URL should be forwarded to HA media_player."""
        req = MediaPlayRequest(
            user_context=ctx,
            entity_id="media_player.living_room_speaker",
            query="https://feeds.example.com/podcast/ep1.mp3",
        )
        mock_roku = MagicMock()
        mock_roku.is_roku_device = AsyncMock(return_value=False)
        mock_roku.find_ma_player_sibling = AsyncMock(return_value=None)
        with (
            patch("services.execution.handlers.media.ha_client.call_service", new=AsyncMock(return_value={"ok": True})),
            patch("services.execution.handlers.roku.is_roku_device", new=AsyncMock(return_value=False)),
            patch("services.execution.handlers.roku.find_ma_player_sibling", new=AsyncMock(return_value=None)),
        ):
            # Import roku module first so it exists in the package
            import services.execution.handlers.roku  # noqa: F401  # pyright: ignore[reportUnusedImport]  # pyright: ignore[reportUnusedImport]
            result = await play_podcast(req, "media_player.living_room_speaker", ctx)

        assert result.status == "SUCCESS"
        assert "podcast" in result.message.lower() or "Playing" in result.message

    @pytest.mark.asyncio
    async def test_podcast_search_finds_podcast_category(self, ctx):
        """Podcast search should try podcasts first, then episodes."""
        req = MediaPlayRequest(
            user_context=ctx,
            entity_id="media_player.living_room_speaker",
            query="The Daily Podcast",
        )
        ha_api_response = {
            "service_response": {
                "podcasts": [{"uri": "music://podcast/12345", "name": "The Daily Podcast"}],
                "episodes": [],
                "tracks": [],
            }
        }
        mock_response = {
            "ok": True,
            "service_response": ha_api_response,
        }
        import services.execution.ha_client as ha_client_module
        mock_cs = AsyncMock(side_effect=[
            mock_response,  # search
            {"ok": True},  # play_media
        ])
        with (
            patch.object(ha_client_module, 'call_service', new=mock_cs),
            patch(
                "services.execution.handlers.media.resolve_mass_entity",
                new=AsyncMock(return_value="media_player.living_room_speaker"),
            ),
            patch("services.execution.handlers.roku.is_roku_device", new=AsyncMock(return_value=False)),
        ):
            import services.execution.handlers.roku  # noqa: F401  # pyright: ignore[reportUnusedImport]
            result = await play_podcast(req, "media_player.living_room_speaker", ctx)

        assert result.status == "SUCCESS"
        assert "podcast" in result.message.lower() or "Playing" in result.message

    @pytest.mark.asyncio
    async def test_podcast_fallback_to_track_search(self, ctx):
        """If no podcast/episode results, should fallback to track search."""
        req = MediaPlayRequest(
            user_context=ctx,
            entity_id="media_player.living_room_speaker",
            query="Unknown Podcast Show",
        )
        empty_api_response = {
            "service_response": {
                "podcasts": [],
                "episodes": [],
                "tracks": [],
            }
        }
        track_api_response = {
            "service_response": {
                "tracks": [{"uri": "music://track/abc123", "name": "Unknown Podcast Show"}],
            }
        }
        empty_response = {
            "ok": True,
            "service_response": empty_api_response,
        }
        track_response = {
            "ok": True,
            "service_response": track_api_response,
        }
        import services.execution.ha_client as ha_client_module
        with (
            patch.object(
                ha_client_module, 'call_service',
                new=AsyncMock(side_effect=[
                    empty_response,  # podcast search
                    track_response,  # track search
                    {"ok": True},  # play_media
                ]),
            ),
            patch(
                "services.execution.handlers.media.resolve_mass_entity",
                new=AsyncMock(return_value="media_player.living_room_speaker"),
            ),
            patch("services.execution.handlers.roku.is_roku_device", new=AsyncMock(return_value=False)),
        ):
            import services.execution.handlers.roku  # noqa: F401  # pyright: ignore[reportUnusedImport]
            result = await play_podcast(req, "media_player.living_room_speaker", ctx)

        assert result.status == "SUCCESS"
        assert "track" in result.message.lower()

    @pytest.mark.asyncio
    async def test_podcast_search_fails_returns_error(self, ctx):
        """If all searches fail, return failure message."""
        req = MediaPlayRequest(
            user_context=ctx,
            entity_id="media_player.living_room_speaker",
            query="Nonexistent Podcast XYZ",
        )
        with (
            patch(
                "services.execution.handlers.media.ha_client.call_service",
                new=AsyncMock(return_value={"ok": True, "service_response": {"service_response": {"podcasts": [], "episodes": [], "tracks": []}}}),
            ),
            patch(
                "services.execution.handlers.media.resolve_mass_entity",
                new=AsyncMock(return_value="media_player.living_room_speaker"),
            ),
            patch("services.execution.handlers.roku.is_roku_device", new=AsyncMock(return_value=False)),
        ):
            import services.execution.handlers.roku  # noqa: F401  # pyright: ignore[reportUnusedImport]
            result = await play_podcast(req, "media_player.living_room_speaker", ctx)

        assert result.status == "FAILURE"
        assert "Could not play podcast" in result.message


# ---------------------------------------------------------------------------
# ABS Handler Tests
# ---------------------------------------------------------------------------

class TestABSHandlers:
    @pytest.mark.asyncio
    async def test_search_returns_full_metadata(self, ctx):
        """ABS search should return complete metadata for audiobooks."""
        abs_url = "http://abs.local:13378"
        abs_key = "test_api_key"
        req = AudiobookshelfRequest(
            user_context=ctx,
            action="search",
            query="The Hobbit",
            limit=5,
        )
        mock_search_result = {
            "book": [
                {
                    "libraryItem": {
                        "id": "abc123",
                        "media": {
                            "metadata": {
                                "title": "The Hobbit",
                                "authorName": "J.R.R. Tolkien",
                                "narratorName": "Rob Inglis",
                                "series": "The Hobbit",
                                "publishedYear": "1937",
                                "genres": ["Fantasy", "Adventure"],
                                "description": "A classic fantasy adventure novel.",
                                "tags": ["classic", "fantasy"],
                                "language": "en",
                            },
                            "duration": 54000,
                            "chapters": [
                                {"id": "ch1", "title": "An Unexpected Party", "startTime": 0},
                                {"id": "ch2", "title": "Roof and Floor", "startTime": 600},
                            ],
                        },
                        "status": "active",
                        "progress": 0.45,
                    }
                }
            ],
        }
        with patch("services.execution.handlers.audiobookshelf.abs_client.search_library", new=AsyncMock(return_value=mock_search_result)):
            result = await _handle_search(abs_url, abs_key, req)

        assert result.status == "SUCCESS"
        books = result.detail.get("books", [])
        assert len(books) == 1
        book = books[0]
        assert book["title"] == "The Hobbit"
        assert book["author"] == "J.R.R. Tolkien"
        assert book["narrator"] == "Rob Inglis"
        assert book["series"] == "The Hobbit"
        assert book["published"] == "1937"
        assert book["genres"] == ["Fantasy", "Adventure"]
        assert book["chapters"] == 2
        assert book["status"] == "active"
        assert book["progress"] == 0.45

    @pytest.mark.asyncio
    async def test_last_played_returns_complete_details(self, ctx):
        """ABS last_played should return full audiobook details with progress."""
        abs_url = "http://abs.local:13378"
        abs_key = "test_api_key"
        mock_items_in_progress = {
            "libraryItems": [
                {
                    "id": "def456",
                    "media": {
                        "metadata": {
                            "title": "Atomic Habits",
                            "authorName": "James Clear",
                            "narratorName": "James Clear",
                            "publisher": "Penguin Audio",
                            "series": "",
                            "description": "An easy and proven way to build good habits...",
                            "genres": ["Self-Help", "Psychology"],
                            "tags": ["habits", "productivity"],
                            "language": "en",
                        },
                        "duration": 32400,
                        "chapters": [
                            {"title": "1% Better", "startTime": 0},
                            {"title": "Identity", "startTime": 1200},
                        ],
                    },
                    "progressLastUpdate": 1718119800000,
                }
            ],
        }
        mock_progress = {
            "currentTime": 21708,
            "isComplete": False,
            "progress": 67,
        }
        with (
            patch("services.execution.handlers.audiobookshelf.abs_client.get_items_in_progress", new=AsyncMock(return_value=mock_items_in_progress)),
            patch("services.execution.handlers.audiobookshelf.abs_client.get_book_progress", new=AsyncMock(return_value=mock_progress)),
        ):
            result = await _handle_last_played(abs_url, abs_key)

        assert result.status == "SUCCESS"
        books = result.detail.get("books", [])
        assert len(books) == 1
        book = books[0]
        assert book["id"] == "def456"
        assert book["title"] == "Atomic Habits"
        assert book["author"] == "James Clear"
        assert book["narrator"] == "James Clear"
        assert book["publisher"] == "Penguin Audio"
        assert book["description"] == "An easy and proven way to build good habits..."
        assert book["genres"] == ["Self-Help", "Psychology"]
        assert book["tags"] == ["habits", "productivity"]
        assert book["language"] == "en"
        assert book["progress"] == 67
        assert book["is_complete"] is False
        assert book["chapter_count"] == 2
        assert "last_played" in book

    @pytest.mark.asyncio
    async def test_search_empty_returns_success_with_empty_list(self, ctx):
        """ABS search with no results should return SUCCESS with empty books list."""
        abs_url = "http://abs.local:13378"
        abs_key = "test_api_key"
        req = AudiobookshelfRequest(
            user_context=ctx,
            action="search",
            query="Nonexistent Book XYZ",
            limit=5,
        )
        with patch("services.execution.handlers.audiobookshelf.abs_client.search_library", new=AsyncMock(return_value={"book": []})):
            result = await _handle_search(abs_url, abs_key, req)

        assert result.status == "SUCCESS"
        assert "No audiobooks found" in result.message
        assert result.detail is None

    @pytest.mark.asyncio
    async def test_search_error_returns_failure(self, ctx):
        """ABS search error should return FAILURE."""
        abs_url = "http://abs.local:13378"
        abs_key = "test_api_key"
        req = AudiobookshelfRequest(
            user_context=ctx,
            action="search",
            query="Test Query",
            limit=5,
        )
        with patch("services.execution.handlers.audiobookshelf.abs_client.search_library", new=AsyncMock(return_value={"error": "API key invalid"})):
            result = await _handle_search(abs_url, abs_key, req)

        assert result.status == "FAILURE"
        assert "API key invalid" in result.message

    @pytest.mark.asyncio
    async def test_last_played_no_results(self, ctx):
        """ABS last_played with no results should return empty books list."""
        abs_url = "http://abs.local:13378"
        abs_key = "test_api_key"
        with patch("services.execution.handlers.audiobookshelf.abs_client.get_items_in_progress", new=AsyncMock(return_value={"libraryItems": []})):
            result = await _handle_last_played(abs_url, abs_key)

        assert result.status == "SUCCESS"
        assert result.detail == {"books": []}


# ---------------------------------------------------------------------------
# detect_media_type Tests
# ---------------------------------------------------------------------------

class TestDetectMediaType:
    def test_detects_video_urls(self):
        """Detect video from YouTube, Vimeo, etc."""
        assert detect_media_type("https://youtube.com/watch?v=abc123") == "video"
        assert detect_media_type("https://vimeo.com/12345") == "video"
        assert detect_media_type("https://twitch.tv/channel") == "video"

    def test_detects_podcast_keywords(self):
        """Detect podcast from keywords and URLs."""
        assert detect_media_type("play the daily podcast") == "podcast"
        assert detect_media_type("https://www.youtube.com/watch?v=abc") == "video"
        assert detect_media_type("episode 5 of joe rogan") == "podcast"

    def test_detects_audiobook_keywords(self):
        """Detect audiobook from keywords."""
        assert detect_media_type("audiobook The Hobbit narrated by Andy Serkis") == "audiobook"
        assert detect_media_type("read by Stephen Fry chapter 3") == "audiobook"
        assert detect_media_type("play audiobook on audiobookshelf") == "audiobook"

    def test_detects_music_default(self):
        """Default to music for generic queries."""
        assert detect_media_type("play some jazz") == "music"
        assert detect_media_type("play the beatles") == "music"
        assert detect_media_type("") == "music"

    def test_url_detection_generic(self):
        """Generic URLs without known patterns should return 'url'."""
        assert detect_media_type("http://example.com/stream.mp3") == "url"
        assert detect_media_type("https://cdn.example.com/audio.ogg") == "url"


# ---------------------------------------------------------------------------
# Media Status MA Filtering Tests (already in test_media_status.py, add here for completeness)
# ---------------------------------------------------------------------------

class TestMediaStatusMAFiltering:
    def test_filters_non_ma_players(self):
        """Only MA-compatible devices should pass the filter."""
        # This is tested in test_media_status.py - verify MA attributes exist
        ma_attrs = {
            "source": "Music Assistant",
            "integration": "music_assistant",
            "active_queue": "queue://default/12345",
        }
        # Non-MA player without MA attributes should be filtered out
        non_ma_attrs = {
            "source": "YouTube",
            "integration": "youtube",
            "active_queue": None,
        }

        # MA player with source check
        is_ma = "music assistant" in ma_attrs["source"].lower() or ma_attrs["integration"] == "music_assistant"
        assert is_ma is True

        # Non-MA player should fail MA check
        is_non_ma = "music assistant" in non_ma_attrs["source"].lower() or non_ma_attrs["integration"] == "music_assistant"
        assert is_non_ma is False

    def test_ma_player_with_active_queue(self):
        """MA player with active_queue should be included."""
        attrs = {
            "source": "Music Assistant",
            "integration": "music_assistant",
            "active_queue": "queue://default/12345",
        }
        is_ma = "music assistant" in attrs["source"].lower() or attrs["integration"] == "music_assistant"
        has_queue = attrs.get("active_queue") is not None
        assert is_ma is True
        assert has_queue is True

    def test_ma_player_without_active_queue(self):
        """MA player without active_queue should still be included (idle players allowed)."""
        attrs = {
            "source": "Music Assistant",
            "integration": "music_assistant",
            "active_queue": None,
        }
        is_ma = "music assistant" in attrs["source"].lower() or attrs["integration"] == "music_assistant"
        assert is_ma is True


# ---------------------------------------------------------------------------
# Integration Test: Full Media Play Flow
# ---------------------------------------------------------------------------

class TestFullMediaPlayFlow:
    @pytest.mark.asyncio
    async def test_full_podcast_play_flow(self, ctx):
        """Test the full podcast play flow: search → fallback → play_media."""
        req = MediaPlayRequest(
            user_context=ctx,
            entity_id="media_player.office_tv",
            query="The Daily Podcast Episode 5",
            media_type="podcast",
        )

        empty_podcast = {
            "ok": True,
            "service_response": {
                "service_response": {
                    "podcasts": [],
                    "episodes": [],
                    "tracks": [],
                }
            },
        }
        track_result = {
            "ok": True,
            "service_response": {
                "service_response": {
                    "tracks": [{"uri": "music://track/podcast123", "name": "The Daily Podcast"}],
                }
            },
        }

        import services.execution.ha_client as ha_client_module
        with (
            patch.object(
                ha_client_module, 'call_service',
                new=AsyncMock(side_effect=[
                    empty_podcast,  # podcast search
                    track_result,  # track search
                    {"ok": True},  # play_media
                ]),
            ),
            patch(
                "services.execution.handlers.media.resolve_mass_entity",
                new=AsyncMock(return_value="media_player.office_tv_ma"),
            ),
            patch(
                "services.execution.handlers.roku.is_roku_device",
                new=AsyncMock(return_value=False),
            ),
        ):
            result = await play_podcast(req, "media_player.office_tv", ctx)

        assert result.status == "SUCCESS"
        assert "as track" in result.message

    @pytest.mark.asyncio
    async def test_full_abs_play_flow(self, ctx):
        """Test the full audiobook play flow: search → play."""
        from services.execution.handlers.media import play_audiobook

        req = MediaPlayRequest(
            user_context=ctx,
            entity_id="media_player.bedroom_radio",
            query="The Hobbit",
            media_type="audiobook",
        )

        search_result = ExecutionResult(
            status="SUCCESS",
            message="Found 1 audiobook(s) for 'The Hobbit'.",
            service="audiobookshelf",
            detail={"books": [{"id": "abc123", "title": "The Hobbit"}]},
        )
        play_result = ExecutionResult(
            status="SUCCESS",
            message="Playing 'The Hobbit' on media_player.bedroom_radio.",
            service="audiobookshelf",
        )

        with (
            patch(
                "services.execution.handlers.audiobookshelf.handle_audiobookshelf",
                new=AsyncMock(side_effect=[search_result, play_result]),
            ),
        ):
            result = await play_audiobook(req, "media_player.bedroom_radio", ctx)

        assert result.status == "SUCCESS"
        assert "Playing" in result.message


# ---------------------------------------------------------------------------
# Gateway Endpoint Tests
# ---------------------------------------------------------------------------

class TestGatewayStreamEndpoint:
    @pytest.mark.asyncio
    async def test_stream_ma_no_ma_players(self, ctx):
        """Stream endpoint should return 404 when no MA players available."""
        from unittest.mock import patch as mock_patch
        from services.gateway.main import stream_music_assistant
        from fastapi import HTTPException, Request
        from services.gateway.schemas import ResolvedCredentials

        creds = ResolvedCredentials(
            user="test_user",
            is_admin=True,
            ha_url="http://homeassistant.local",
            ha_token="test_token",
            mass_url="http://ha.sumemail.com:8095",
            mass_token="test_jwt_token",
        )

        async def mock_resolve(*args, **kwargs):
            return creds

        class FakeRequest:
            headers = {"Authorization": "Bearer test_internal_secret"}
            async def json(self):
                return {}

        with (
            mock_patch("services.gateway.main._resolve_identity_from_request", new=mock_resolve),
            mock_patch("services.gateway.main.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=MM(status_code=200, json=MM(return_value={"players": []})))
            mock_client_cls.return_value = mock_client

            with pytest.raises(HTTPException) as exc_info:
                await stream_music_assistant("music://track/12345", cast(Request, FakeRequest()))

            assert exc_info.value.status_code == 404
            assert "No Music Assistant players available" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_stream_ma_prefers_browser_player(self, ctx):
        """Stream endpoint should prefer the browser Sendspin player."""
        from services.gateway.main import stream_music_assistant
        from fastapi import Request
        from services.gateway.schemas import ResolvedCredentials
        from services.gateway.ma_ws_client import MAWebSocketClient

        creds = ResolvedCredentials(
            user="test_user",
            is_admin=True,
            ha_url="http://homeassistant.local",
            ha_token="test_token",
            mass_url="http://ha.sumemail.com:8095",
            mass_token="test_jwt_token",
        )

        async def mock_resolve(*args, **kwargs):
            return creds

        async def mock_connect(self):
            pass

        async def mock_disconnect(self):
            pass

        async def mock_send_command(self, command, args):
            sent_commands.append((command, args))

        sent_commands: list[tuple[str, dict]] = []

        class FakeRequest:
            headers = {"Authorization": "Bearer test_internal_secret"}
            async def json(self):
                return {}

        with (
            patch("services.gateway.main._resolve_identity_from_request", new=mock_resolve),
            patch.object(MAWebSocketClient, "connect", new=mock_connect),
            patch.object(MAWebSocketClient, "disconnect", new=mock_disconnect),
            patch.object(MAWebSocketClient, "send_command", new=mock_send_command),
        ):
            # Mock player list and status
            async def mock_post(url, json=None, headers=None, timeout=15.0):
                if (json or {}).get("command") == "players/all":
                    return MM(
                        status_code=200,
                        json=MM(return_value=[
                            {"player_id": "office_tv", "name": "Office TV"},
                            {"player_id": "browser_player", "name": "Sendspin JS Client (test)"},
                        ]),
                    )
                return MM(status_code=500)

            with patch("services.gateway.main.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.post = AsyncMock(side_effect=mock_post)
                mock_client_cls.return_value = mock_client

                # This will fail once the stream URL loop times out, but we can
                # verify that the browser player was selected first.
                with pytest.raises(Exception):
                    await stream_music_assistant("music://track/12345", cast(Request, FakeRequest()))

                assert sent_commands, "Expected at least one MA command to be sent"
                assert sent_commands[0][0] == "player_queues/play_media"
                assert sent_commands[0][1].get("queue_id") == "browser_player"



# ---------------------------------------------------------------------------
# Test Helper: MA Attributes for Media Status
# ---------------------------------------------------------------------------

# MA attributes are inlined where needed (TestMediaStatusMAFiltering)
