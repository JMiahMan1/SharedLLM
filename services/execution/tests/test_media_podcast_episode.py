"""BUG-26 / P2-T17: podcast playback must resolve a real episode.

Live ABS 2.x removed the legacy ``/api/items/:id/stream`` route. Podcast
playback now:
1. resolves the podcast's episodes via ``GET /api/items/{id}?expanded=1``
   -> ``media.episodes``,
2. picks the requested episode (title match) or the latest by ``publishedAt``,
3. starts a playback session (``POST /api/items/{podcast}/play/{episode}``),
4. hands the device a gateway-routed, mt-token-signed HLS URL
   (``/api/media/stream/abs-session/{sid}/0``) — never the raw ABS key.
Also: the Nextcloud fallback must pass ``ctx`` (the UserContext), not a
nonexistent ``ctx.user_context`` attribute.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.execution.handlers.audiobookshelf import handle_audiobookshelf
from services.execution.handlers.media import play_podcast
from services.execution.schemas import (
    AudiobookshelfRequest,
    ExecutionResult,
    MediaPlayRequest,
    UserContext,
)

EP_OLD = {"id": "ep-1", "title": "Old Episode", "publishedAt": 1000}
EP_NEW = {"id": "ep-2", "title": "New Episode", "publishedAt": 2000}
EP_THEMED = {"id": "ep-3", "title": "The Deep Dive", "publishedAt": 500}

PODCAST_ITEM = {
    "id": "pod-1",
    "media": {"episodes": [EP_OLD, EP_NEW, EP_THEMED]},
}


def _ctx() -> UserContext:
    return UserContext(
        user="testuser",
        ha_url="http://ha.local",
        ha_token="ha-token",
        audiobookshelf_url="http://abs.local",
        audiobookshelf_api_key="abs-key",
    )


def _media_ctx() -> UserContext:
    return _ctx()


class TestPlayPodcastEpisodeAction:
    @pytest.mark.asyncio
    async def test_resolves_latest_episode_and_streams_via_gateway(self):
        ctx = _ctx()
        req = AudiobookshelfRequest(
            action="play_podcast_episode",
            book_id="pod-1",
            entity_id="media_player.tv",
            user_context=ctx,
        )
        with (
            patch(
                "services.execution.abs_client.get_book",
                new=AsyncMock(return_value=PODCAST_ITEM),
            ),
            patch(
                "services.execution.abs_client.start_playback_session",
                new=AsyncMock(return_value={"id": "sess-1"}),
            ) as mock_session,
            patch(
                "services.execution.ha_client.get_state",
                new=AsyncMock(
                    return_value={
                        "state": "playing",
                        "attributes": {"media_title": "New Episode"},
                    }
                ),
            ),
            patch("services.execution.handlers.audiobookshelf.PLAYBACK_VERIFY_INTERVAL", 0),
            patch("services.execution.handlers.roku.is_roku_device", new=AsyncMock(return_value=False)),
            patch(
                "services.execution.ha_client.call_service",
                new=AsyncMock(return_value={"ok": True}),
            ) as mock_call,
        ):
            result = await handle_audiobookshelf(req)

        assert result.status == "SUCCESS"
        mock_session.assert_awaited_once_with("http://abs.local", "abs-key", "pod-1", "ep-2")
        data = mock_call.call_args.args[5]
        assert data["media_content_type"] == "application/x-mpegurl"
        url = data["media_content_id"]
        assert "/api/media/stream/abs-session/sess-1/0" in url
        assert "user=testuser" in url
        assert "mt=" in url
        assert "abs-key" not in url

    @pytest.mark.asyncio
    async def test_requested_episode_matched_by_title(self):
        ctx = _ctx()
        req = AudiobookshelfRequest(
            action="play_podcast_episode",
            book_id="pod-1",
            query="deep dive",
            entity_id="media_player.tv",
            user_context=ctx,
        )
        with (
            patch(
                "services.execution.abs_client.get_book",
                new=AsyncMock(return_value=PODCAST_ITEM),
            ),
            patch(
                "services.execution.abs_client.start_playback_session",
                new=AsyncMock(return_value={"id": "sess-2"}),
            ) as mock_session,
            patch(
                "services.execution.ha_client.get_state",
                new=AsyncMock(
                    return_value={
                        "state": "playing",
                        "attributes": {"media_title": "New Episode"},
                    }
                ),
            ),
            patch("services.execution.handlers.audiobookshelf.PLAYBACK_VERIFY_INTERVAL", 0),
            patch("services.execution.handlers.roku.is_roku_device", new=AsyncMock(return_value=False)),
            patch("services.execution.ha_client.call_service", new=AsyncMock(return_value={"ok": True})),
        ):
            result = await handle_audiobookshelf(req)

        assert result.status == "SUCCESS"
        mock_session.assert_awaited_once_with("http://abs.local", "abs-key", "pod-1", "ep-3")

    @pytest.mark.asyncio
    async def test_explicit_episode_id_is_validated_against_item(self):
        ctx = _ctx()
        req = AudiobookshelfRequest(
            action="play_podcast_episode",
            book_id="pod-1",
            episode_id="ep-1",
            entity_id="media_player.tv",
            user_context=ctx,
        )
        with (
            patch(
                "services.execution.abs_client.get_book",
                new=AsyncMock(return_value=PODCAST_ITEM),
            ),
            patch(
                "services.execution.abs_client.start_playback_session",
                new=AsyncMock(return_value={"id": "sess-3"}),
            ) as mock_session,
            patch(
                "services.execution.ha_client.get_state",
                new=AsyncMock(
                    return_value={
                        "state": "playing",
                        "attributes": {"media_title": "New Episode"},
                    }
                ),
            ),
            patch("services.execution.handlers.audiobookshelf.PLAYBACK_VERIFY_INTERVAL", 0),
            patch("services.execution.handlers.roku.is_roku_device", new=AsyncMock(return_value=False)),
            patch("services.execution.ha_client.call_service", new=AsyncMock(return_value={"ok": True})),
        ):
            result = await handle_audiobookshelf(req)

        assert result.status == "SUCCESS"
        mock_session.assert_awaited_once_with("http://abs.local", "abs-key", "pod-1", "ep-1")

    @pytest.mark.asyncio
    async def test_unknown_explicit_episode_fails(self):
        ctx = _ctx()
        req = AudiobookshelfRequest(
            action="play_podcast_episode",
            book_id="pod-1",
            episode_id="ep-missing",
            entity_id="media_player.tv",
            user_context=ctx,
        )
        with (
            patch(
                "services.execution.abs_client.get_book",
                new=AsyncMock(return_value=PODCAST_ITEM),
            ),
            patch("services.execution.ha_client.call_service", new=AsyncMock(return_value={"ok": True})),
        ):
            result = await handle_audiobookshelf(req)

        assert result.status == "FAILURE"
        assert "not found" in result.message.lower()

    @pytest.mark.asyncio
    async def test_no_episodes_fails(self):
        ctx = _ctx()
        req = AudiobookshelfRequest(
            action="play_podcast_episode",
            book_id="pod-1",
            entity_id="media_player.tv",
            user_context=ctx,
        )
        with patch(
            "services.execution.abs_client.get_book",
            new=AsyncMock(return_value={"id": "pod-1", "media": {"episodes": []}}),
        ):
            result = await handle_audiobookshelf(req)

        assert result.status == "FAILURE"
        assert "no episodes" in result.message.lower()

    @pytest.mark.asyncio
    async def test_expanded_item_lookup_error_fails(self):
        ctx = _ctx()
        req = AudiobookshelfRequest(
            action="play_podcast_episode",
            book_id="pod-1",
            entity_id="media_player.tv",
            user_context=ctx,
        )
        with patch(
            "services.execution.abs_client.get_book",
            new=AsyncMock(return_value={"error": "ABS returned 500: boom"}),
        ):
            result = await handle_audiobookshelf(req)

        assert result.status == "FAILURE"
        assert "boom" in result.message

    @pytest.mark.asyncio
    async def test_session_start_failure_fails(self):
        ctx = _ctx()
        req = AudiobookshelfRequest(
            action="play_podcast_episode",
            book_id="pod-1",
            entity_id="media_player.tv",
            user_context=ctx,
        )
        with (
            patch(
                "services.execution.abs_client.get_book",
                new=AsyncMock(return_value=PODCAST_ITEM),
            ),
            patch(
                "services.execution.abs_client.start_playback_session",
                new=AsyncMock(return_value={"error": "ABS returned 403: forbidden"}),
            ),
        ):
            result = await handle_audiobookshelf(req)

        assert result.status == "FAILURE"
        assert "session" in result.message.lower()

    @pytest.mark.asyncio
    async def test_entity_id_required(self):
        ctx = _ctx()
        req = AudiobookshelfRequest(
            action="play_podcast_episode",
            book_id="pod-1",
            user_context=ctx,
        )
        result = await handle_audiobookshelf(req)
        assert result.status == "FAILURE"
        assert "entity_id" in result.message


class TestMediaPlayPodcastRoutesToEpisodeAction:
    @pytest.mark.asyncio
    async def test_abs_branch_uses_play_podcast_episode(self):
        ctx = _media_ctx()
        req = MediaPlayRequest(user_context=ctx, entity_id="media_player.tv", query="some show")
        calls: list[AudiobookshelfRequest] = []

        async def fake_abs(request: AudiobookshelfRequest) -> ExecutionResult:
            calls.append(request)
            if request.action == "search":
                return ExecutionResult(
                    status="SUCCESS",
                    message="search",
                    service="audiobookshelf",
                    detail={"podcasts": [{"id": "pod-1", "title": "Some Show"}], "books": []},
                )
            return ExecutionResult(status="SUCCESS", message="ok", service="audiobookshelf")

        with patch(
            "services.execution.handlers.audiobookshelf.handle_audiobookshelf",
            new=AsyncMock(side_effect=fake_abs),
        ):
            result = await play_podcast(req, "media_player.tv", ctx)

        assert result.status == "SUCCESS"
        play_call = next(c for c in calls if c.action != "search")
        assert play_call.action == "play_podcast_episode"
        assert play_call.book_id == "pod-1"
        assert play_call.entity_id == "media_player.tv"


class TestNextcloudFallbackCredentialArg:
    @pytest.mark.asyncio
    async def test_resolve_credentials_receives_ctx(self):
        """BUG-26: the fallback read ``ctx.user_context`` (AttributeError,
        swallowed) — it must pass the UserContext itself."""
        ctx = _media_ctx()
        req = MediaPlayRequest(user_context=ctx, entity_id="media_player.tv", query="nc show")
        from services.execution import nextcloud_client

        mock_resolve = MagicMock(return_value=(None, None, None))
        empty_search = {
            "ok": True,
            "service_response": {"service_response": {"podcasts": [], "episodes": [], "tracks": []}},
        }
        with (
            patch(
                "services.execution.handlers.audiobookshelf.handle_audiobookshelf",
                new=AsyncMock(
                    return_value=ExecutionResult(status="FAILURE", message="no abs", service="audiobookshelf")
                ),
            ),
            patch("services.execution.ha_client.find_mass_config_entry", new=AsyncMock(return_value="ce-1")),
            patch(
                "services.execution.handlers.media.resolve_mass_entity",
                new=AsyncMock(return_value="media_player.tv"),
            ),
            patch("services.execution.handlers.roku.is_roku_device", new=AsyncMock(return_value=False)),
            patch("services.execution.ha_client.call_service", new=AsyncMock(return_value=empty_search)),
            patch.object(nextcloud_client, "resolve_credentials", new=mock_resolve),
        ):
            result = await play_podcast(req, "media_player.tv", ctx)

        assert mock_resolve.call_count == 1
        assert mock_resolve.call_args.args[0] is ctx
        assert result.status == "FAILURE"
