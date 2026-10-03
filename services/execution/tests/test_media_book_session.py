"""Book playback must use an ABS 2.x playback session, not the removed /stream route.

Live ABS 2.x deleted ``GET /api/items/:id/stream`` (404 live), so both
``play`` and ``resume`` must start a session
(``POST /api/items/{bookId}/play``) and hand the device the gateway-routed,
mt-token-signed HLS URL for ``/public/session/{sid}/track/{i}``. Book track
indexes come from the expanded item's ``media.tracks`` (1-based), and the
playlist is only written once ABS starts transcoding — the gateway polls
(BUG-33).
"""
import pathlib
from unittest.mock import AsyncMock, patch

import pytest

from services.execution.handlers.audiobookshelf import handle_audiobookshelf
from services.execution.schemas import AudiobookshelfRequest, UserContext

BOOK_ITEM = {
    "id": "book-1",
    "media": {
        "metadata": {"title": "God's Smuggler (Unabridged)"},
        "tracks": [{"index": 1, "title": "God's Smuggler.mp3", "duration": 31745.1}],
    },
}

MULTIFILE_BOOK = {
    "id": "book-2",
    "media": {
        "metadata": {"title": "Two File Book"},
        "tracks": [
            {"index": 1, "title": "part 1", "duration": 100},
            {"index": 2, "title": "part 2", "duration": 200},
        ],
    },
}


def _ctx() -> UserContext:
    return UserContext(
        user="testuser",
        ha_url="http://ha.local",
        ha_token="ha-token",
        audiobookshelf_url="http://abs.local",
        audiobookshelf_api_key="abs-key",
    )


def _play_req(**kw) -> AudiobookshelfRequest:
    fields = {
        "action": "play",
        "book_id": "book-1",
        "entity_id": "media_player.tv",
        "user_context": _ctx(),
    }
    fields.update(kw)
    return AudiobookshelfRequest(**fields)


PLAYING_STATE = {
    "state": "playing",
    "attributes": {"media_title": "God's Smuggler (Unabridged)", "media_content_type": "audiobook"},
}
IDLE_STATE = {"state": "idle", "attributes": {}}


def _patches(book=BOOK_ITEM, session=None, call_result=None, state=PLAYING_STATE):
    return (
        patch("services.execution.abs_client.get_book", new=AsyncMock(return_value=book)),
        patch(
            "services.execution.abs_client.start_playback_session",
            new=AsyncMock(return_value=session if session is not None else {"id": "sess-b1"}),
        ),
        patch("services.execution.ha_client.get_state", new=AsyncMock(return_value=state)),
        patch("services.execution.handlers.roku.is_roku_device", new=AsyncMock(return_value=False)),
        patch(
            "services.execution.ha_client.call_service",
            new=AsyncMock(return_value=call_result or {"ok": True}),
        ),
        patch("services.execution.handlers.audiobookshelf.PLAYBACK_VERIFY_INTERVAL", 0),
    )


class TestBookPlaybackSession:
    @pytest.mark.asyncio
    async def test_play_book_starts_session_and_streams_hls(self):
        p = _patches()
        with p[0], p[1] as mock_session, p[2], p[3], p[4] as mock_call, p[5]:
            result = await handle_audiobookshelf(_play_req())

        assert result.status == "SUCCESS"
        mock_session.assert_awaited_once_with("http://abs.local", "abs-key", "book-1", None)
        data = mock_call.call_args.args[5]
        assert data["media_content_type"] == "application/x-mpegurl"
        url = data["media_content_id"]
        assert "/api/media/stream/abs-session/sess-b1/1" in url
        assert "user=testuser" in url
        assert "mt=" in url
        assert "abs-key" not in url
        assert "audiobookshelf/" not in url

    @pytest.mark.asyncio
    async def test_play_book_reports_the_real_title(self):
        p = _patches()
        with p[0], p[1], p[2], p[3], p[4], p[5]:
            result = await handle_audiobookshelf(_play_req())
        assert "God's Smuggler (Unabridged)" in result.message
        assert "book-1" not in result.message

    @pytest.mark.asyncio
    async def test_track_index_comes_from_the_expanded_item(self):
        p = _patches(book=MULTIFILE_BOOK)
        with p[0], p[1], p[2], p[3], p[4] as mock_call, p[5]:
            result = await handle_audiobookshelf(_play_req(book_id="book-2"))
        assert result.status == "SUCCESS"
        assert "/api/media/stream/abs-session/sess-b1/1" in mock_call.call_args.args[5]["media_content_id"]

    @pytest.mark.asyncio
    async def test_multifile_book_with_first_track_missing_fails_loudly(self):
        book = {"id": "book-3", "media": {"metadata": {"title": "No Tracks"}, "tracks": []}}
        p = _patches(book=book)
        with p[0], p[1] as mock_session, p[2], p[3], p[4] as mock_call, p[5]:
            result = await handle_audiobookshelf(_play_req(book_id="book-3"))
        assert result.status == "FAILURE"
        assert "track" in result.message.lower()
        mock_session.assert_not_awaited()
        mock_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_item_lookup_error_fails_loudly(self):
        p = _patches(book={"error": "boom"})
        with p[0], p[1] as mock_session, p[2], p[3], p[4], p[5]:
            result = await handle_audiobookshelf(_play_req())
        assert result.status == "FAILURE"
        assert "boom" in result.message
        mock_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_session_error_fails_loudly(self):
        p = _patches(session={"error": "nope"})
        with p[0], p[1], p[2], p[3], p[4] as mock_call, p[5]:
            result = await handle_audiobookshelf(_play_req())
        assert result.status == "FAILURE"
        assert "nope" in result.message
        mock_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_search_path_expands_the_matched_book(self):
        p = _patches()
        with (
            patch(
                "services.execution.abs_client.search_library",
                new=AsyncMock(return_value={"results": [{"id": "book-1", "media": {"metadata": {"title": "Hit"}}}]}),
            ),
            p[0] as mock_get_book,
            p[1] as mock_session,
            p[2],
            p[3],
            p[4] as mock_call,
            p[5],
        ):
            result = await handle_audiobookshelf(
                AudiobookshelfRequest(
                    action="play", query="smuggler", entity_id="media_player.tv", user_context=_ctx()
                )
            )
        assert result.status == "SUCCESS"
        mock_get_book.assert_awaited_once_with("http://abs.local", "abs-key", "book-1")
        mock_session.assert_awaited_once_with("http://abs.local", "abs-key", "book-1", None)
        assert "/api/media/stream/abs-session/sess-b1/1" in mock_call.call_args.args[5]["media_content_id"]

    @pytest.mark.asyncio
    async def test_resume_uses_a_session_too(self):
        progress = {
            "libraryItems": [
                {
                    "id": "book-9",
                    "progressLastUpdate": 1700000000000,
                    "media": {"metadata": {"title": "Half Finished"}},
                }
            ]
        }
        expanded = {
            "id": "book-9",
            "media": {
                "metadata": {"title": "Half Finished"},
                "tracks": [{"index": 1, "duration": 500}],
            },
        }
        p = _patches(book=expanded)
        with (
            patch("services.execution.abs_client.get_items_in_progress", new=AsyncMock(return_value=progress)),
            p[0],
            p[1] as mock_session,
            p[2],
            p[3],
            p[4] as mock_call,
        ):
            result = await handle_audiobookshelf(
                AudiobookshelfRequest(action="resume", entity_id="media_player.tv", user_context=_ctx())
            )
        assert result.status == "SUCCESS"
        mock_session.assert_awaited_once_with("http://abs.local", "abs-key", "book-9", None)
        data = mock_call.call_args.args[5]
        assert data["media_content_type"] == "application/x-mpegurl"
        assert "/api/media/stream/abs-session/sess-b1/1" in data["media_content_id"]

    def test_the_dead_legacy_stream_route_is_gone(self):
        """ABS 2.x 404s /api/items/:id/stream, so no URL builder may return it.

        Structural guard: `abs_client.get_stream_url` and the gateway's
        /api/media/stream/audiobookshelf route were deleted with the last
        caller (the two book paths above). Session-based playback is the only
        way to hand audio to a device now.
        """
        from services.execution import abs_client

        assert not hasattr(abs_client, "get_stream_url")
        source = pathlib.Path(abs_client.__file__).read_text()
        assert "stream/audiobookshelf" not in source
