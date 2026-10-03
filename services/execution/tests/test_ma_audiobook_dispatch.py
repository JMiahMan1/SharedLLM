"""Music Assistant players must get an MA library URI, and playback must be verified.

Two bugs this guards, both found by playing a book on a real MA HASS player:

1. MA is not a URL player. ``_dispatch_stream`` used to hand it the gateway's
   HLS URL, which MA filed as the track *title* — the player reported the tail of
   the URL as ``media_title`` and stayed idle while the call returned SUCCESS.
   MA only accepts ``library://audiobook/<n>``, so the URI must be resolved first
   (services/shared/ma_library.py).

2. A 200 from Home Assistant only means the service call was *accepted*. The old
   code returned SUCCESS on ``result["ok"]`` with no check that anything was
   actually playing, so a silent no-op reported success. Playback is now polled
   and a non-start is a FAILURE carrying the reason.
"""
import pathlib
from unittest.mock import AsyncMock, patch

import pytest

from services.execution.handlers.audiobookshelf import (
    _looks_like_stream_url,
    _verify_playback,
    handle_audiobookshelf,
)
from services.execution.schemas import AudiobookshelfRequest, UserContext

BOOK = {
    "id": "book-1",
    "media": {
        "metadata": {"title": "God's Smuggler (Unabridged)"},
        "tracks": [{"index": 1, "title": "t.mp3", "duration": 31745.1}],
    },
}

# is_music_assistant_player() keys off these attributes.
MA_STATE = {
    "state": "playing",
    "attributes": {
        "source": "Music Assistant Queue",
        "active_queue": "up7c9e874d5efc",
        "media_title": "God's Smuggler (Unabridged)",
    },
}
# What a player looks like after being handed an unresolvable URL: idle, with the
# URL itself as the title. This is the bug being fixed, captured as a fixture.
MA_URL_AS_TITLE_STATE = {
    "state": "idle",
    "attributes": {
        "source": "Music Assistant Queue",
        "media_title": "1?user=testuser&mt=1791056401",
    },
}

RESOLVED = type("R", (), {"ma_uri": "library://audiobook/260", "abs_item_id": "book-1", "title": "Smuggler"})


def _ctx(**kw) -> UserContext:
    fields = {
        "user": "testuser",
        "ha_url": "http://ha.local",
        "ha_token": "ha-token",
        "audiobookshelf_url": "http://abs.local",
        "audiobookshelf_api_key": "abs-key",
        "mass_url": "http://ma.local:8095",
        "mass_token": "ma-token",
    }
    fields.update(kw)
    return UserContext(**fields)


def _req(**kw) -> AudiobookshelfRequest:
    fields = {
        "action": "play",
        "book_id": "book-1",
        "entity_id": "media_player.tv",
        "user_context": _ctx(),
    }
    fields.update(kw)
    return AudiobookshelfRequest(**fields)


def _stack(state=MA_STATE, call_result=None, resolved=RESOLVED, resolver_error=None):
    """Patch the whole ABS->MA path, returning the context managers."""
    resolver = AsyncMock(return_value=resolved) if resolver_error is None else AsyncMock(
        side_effect=resolver_error
    )
    return {
        "get_book": patch("services.execution.abs_client.get_book", new=AsyncMock(return_value=BOOK)),
        "session": patch(
            "services.execution.abs_client.start_playback_session",
            new=AsyncMock(return_value={"id": "sess-b1"}),
        ),
        "state": patch("services.execution.ha_client.get_state", new=AsyncMock(return_value=state)),
        "roku": patch("services.execution.handlers.roku.is_roku_device", new=AsyncMock(return_value=False)),
        "call": patch(
            "services.execution.ha_client.call_service",
            new=AsyncMock(return_value=call_result or {"ok": True}),
        ),
        "resolve": patch(
            "services.execution.handlers.audiobookshelf.ma_library.resolve_audiobook_uri",
            new=resolver,
        ),
        "verify_interval": patch(
            "services.execution.handlers.audiobookshelf.PLAYBACK_VERIFY_INTERVAL", 0
        ),
    }


class TestMusicAssistantGetsALibraryUri:
    @pytest.mark.asyncio
    async def test_ma_player_is_handed_the_resolved_library_uri(self):
        from contextlib import ExitStack

        parts = _stack()
        with ExitStack() as st:
            mocks = {name: st.enter_context(cm) for name, cm in parts.items()}
            result = await handle_audiobookshelf(_req())

        assert result.status == "SUCCESS", result.message
        call = mocks["call"].call_args
        # music_assistant.play_media, not media_player.play_media.
        assert call.args[2] == "music_assistant"
        assert call.args[3] == "play_media"
        data = call.args[5]
        assert data["media_id"] == "library://audiobook/260"
        assert data["enqueue"] == "play"
        # The gateway HLS URL must never reach MA: that is the original bug.
        assert "abs-session" not in str(data)

    @pytest.mark.asyncio
    async def test_no_stream_url_is_sent_to_ma(self):
        from contextlib import ExitStack

        parts = _stack()
        with ExitStack() as st:
            mocks = {name: st.enter_context(cm) for name, cm in parts.items()}
            await handle_audiobookshelf(_req())
        sent = str(mocks["call"].call_args.args[5])
        assert ".m3u8" not in sent
        assert "11435" not in sent

    @pytest.mark.asyncio
    async def test_resolver_is_called_with_the_abs_item_id(self):
        from contextlib import ExitStack

        parts = _stack()
        with ExitStack() as st:
            mocks = {name: st.enter_context(cm) for name, cm in parts.items()}
            await handle_audiobookshelf(_req())
        mocks["resolve"].assert_awaited_once()
        args = mocks["resolve"].await_args.args
        assert args[0] == "http://ma.local:8095"
        assert args[1] == "ma-token"
        assert args[2] == "book-1"
        assert "Smuggler" in args[3]

    @pytest.mark.asyncio
    async def test_unresolvable_book_fails_instead_of_sending_a_bad_uri(self):
        from contextlib import ExitStack
        from services.shared.ma_library import MALibraryLookupError

        err = MALibraryLookupError("Music Assistant does not have that book", reason="not_in_ma_library")
        parts = _stack(resolver_error=err)
        with ExitStack() as st:
            mocks = {name: st.enter_context(cm) for name, cm in parts.items()}
            result = await handle_audiobookshelf(_req())

        assert result.status == "FAILURE"
        assert "does not have that book" in result.message
        mocks["call"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_ma_credentials_fail_loudly(self):
        from contextlib import ExitStack
        from services.shared.ma_library import MALibraryLookupError

        err = MALibraryLookupError("Music Assistant token is not configured", reason="no_mass_token")
        parts = _stack(resolver_error=err)
        req = _req(user_context=_ctx(mass_token=""))
        with ExitStack() as st:
            mocks = {name: st.enter_context(cm) for name, cm in parts.items()}
            result = await handle_audiobookshelf(req)

        assert result.status == "FAILURE"
        assert "not configured" in result.message
        mocks["call"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_ma_player_still_gets_the_hls_url(self):
        """Roku/Chromecast/DLNA/TV cannot resolve an MA URI — they need the URL."""
        from contextlib import ExitStack

        plain = {
            "state": "playing",
            "attributes": {"media_title": "x", "media_content_type": "audiobook"},
        }
        parts = _stack(state=plain)
        with ExitStack() as st:
            mocks = {name: st.enter_context(cm) for name, cm in parts.items()}
            result = await handle_audiobookshelf(_req())

        assert result.status == "SUCCESS", result.message
        call = mocks["call"].call_args
        assert call.args[2] == "media_player"
        assert "abs-session/sess-b1/1" in call.args[5]["media_content_id"]
        # No MA lookup needed when the player is not an MA player.
        mocks["resolve"].assert_not_awaited()


class TestPlaybackIsVerified:
    @pytest.mark.asyncio
    async def test_accepted_call_that_never_plays_is_a_failure(self):
        """The core regression: HA returns ok, the player stays idle."""
        from contextlib import ExitStack

        parts = _stack(state=MA_URL_AS_TITLE_STATE)
        with ExitStack() as st:
            {name: st.enter_context(cm) for name, cm in parts.items()}
            result = await handle_audiobookshelf(_req())

        assert result.status == "FAILURE"
        assert "did not start" in result.message
        assert "abs-session" not in result.message

    @pytest.mark.asyncio
    async def test_failure_explains_that_the_url_became_the_title(self):
        from contextlib import ExitStack

        parts = _stack(state=MA_URL_AS_TITLE_STATE)
        with ExitStack() as st:
            {name: st.enter_context(cm) for name, cm in parts.items()}
            result = await handle_audiobookshelf(_req())

        # An operator reading the log needs to know this is the URL-as-title bug.
        assert "stream URL" in result.message
        assert "title" in result.message

    @pytest.mark.asyncio
    async def test_plain_idle_is_reported_as_never_started(self):
        from contextlib import ExitStack

        parts = _stack(state={"state": "idle", "attributes": {"media_title": "Nothing"}})
        with ExitStack() as st:
            {name: st.enter_context(cm) for name, cm in parts.items()}
            result = await handle_audiobookshelf(_req())

        assert result.status == "FAILURE"
        assert "never started playing" in result.message
        assert "idle" in result.message

    @pytest.mark.asyncio
    async def test_buffering_counts_as_playing(self):
        from contextlib import ExitStack

        buf = {"state": "buffering", "attributes": {"media_title": "God's Smuggler (Unabridged)"}}
        parts = _stack(state=buf)
        with ExitStack() as st:
            {name: st.enter_context(cm) for name, cm in parts.items()}
            result = await handle_audiobookshelf(_req())

        assert result.status == "SUCCESS", result.message

    @pytest.mark.asyncio
    async def test_service_call_failure_is_still_reported(self):
        from contextlib import ExitStack

        parts = _stack(call_result={"ok": False, "error": "entity not found"})
        with ExitStack() as st:
            {name: st.enter_context(cm) for name, cm in parts.items()}
            result = await handle_audiobookshelf(_req())

        assert result.status == "FAILURE"
        assert "entity not found" in result.message

    @pytest.mark.asyncio
    async def test_state_is_polled_until_it_starts(self):
        """A player that takes a moment must not be reported as broken."""
        from contextlib import ExitStack

        idle = {"state": "idle", "attributes": {}}
        playing = {"state": "playing", "attributes": {"media_title": "Smuggler"}}
        get_state = AsyncMock(side_effect=[idle, idle, playing])
        parts = _stack()
        with ExitStack() as st:
            for cm in parts.values():
                st.enter_context(cm)
            st.enter_context(patch("services.execution.ha_client.get_state", new=get_state))
            st.enter_context(
                patch("services.execution.handlers.audiobookshelf.PLAYBACK_VERIFY_INTERVAL", 0)
            )
            result = await handle_audiobookshelf(_req())

        assert result.status == "SUCCESS", result.message
        assert get_state.await_count >= 3

    @pytest.mark.asyncio
    async def test_polling_gives_up_and_says_so(self):
        from contextlib import ExitStack

        idle = {"state": "idle", "attributes": {}}
        get_state = AsyncMock(return_value=idle)
        parts = _stack()
        with ExitStack() as st:
            for cm in parts.values():
                st.enter_context(cm)
            st.enter_context(patch("services.execution.ha_client.get_state", new=get_state))
            st.enter_context(
                patch(
                    "services.execution.handlers.audiobookshelf.PLAYBACK_VERIFY_ATTEMPTS", 3
                )
            )
            st.enter_context(
                patch("services.execution.handlers.audiobookshelf.PLAYBACK_VERIFY_INTERVAL", 0)
            )
            result = await handle_audiobookshelf(_req())

        assert result.status == "FAILURE"
        # Bounded, not an unbounded retry loop: the setup lookups plus exactly
        # PLAYBACK_VERIFY_ATTEMPTS polls.
        assert get_state.await_count <= 3 + 4

    @pytest.mark.asyncio
    async def test_verify_playback_respects_the_attempt_bound(self):
        with patch(
            "services.execution.ha_client.get_state",
            new=AsyncMock(return_value={"state": "idle", "attributes": {}}),
        ) as get_state, patch(
            "services.execution.handlers.audiobookshelf.PLAYBACK_VERIFY_ATTEMPTS", 4
        ), patch(
            "services.execution.handlers.audiobookshelf.PLAYBACK_VERIFY_INTERVAL", 0
        ):
            ok, _ = await _verify_playback("http://ha.local", "t", "media_player.tv")
        assert ok is False
        assert get_state.await_count == 4

    @pytest.mark.asyncio
    async def test_verify_playback_handles_a_missing_state(self):
        with patch("services.execution.ha_client.get_state", new=AsyncMock(return_value=None)), patch(
            "services.execution.handlers.audiobookshelf.PLAYBACK_VERIFY_ATTEMPTS", 2
        ), patch("services.execution.handlers.audiobookshelf.PLAYBACK_VERIFY_INTERVAL", 0):
            ok, detail = await _verify_playback("http://ha.local", "t", "media_player.tv")
        assert ok is False
        assert detail

    @pytest.mark.asyncio
    async def test_verify_playback_returns_the_playing_title(self):
        with patch(
            "services.execution.ha_client.get_state",
            new=AsyncMock(return_value=MA_STATE),
        ):
            ok, detail = await _verify_playback("http://ha.local", "t", "media_player.tv")
        assert ok is True
        assert detail == "God's Smuggler (Unabridged)"


class TestUrlAsTitleDetection:
    @pytest.mark.parametrize(
        "value",
        [
            "1?user=jeremiah&mt=1791056401",
            "http://abs.local/hls/s/output.m3u8",
            "https://example.com/x.ts?mt=abc",
            "output-0.ts?user=x&mt=y",
        ],
    )
    def test_detects_our_url_used_as_a_title(self, value):
        assert _looks_like_stream_url(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "God's Smuggler (Unabridged)",
            "4:12 - Switchfoot",
            "The Chronicles of Narnia",
            "",
            None,
        ],
    )
    def test_real_titles_are_not_flagged(self, value):
        assert _looks_like_stream_url(value) is False


class TestStructuralGuards:
    def test_ma_branch_does_not_label_a_library_uri_as_a_track(self):
        """`media_type: "track"` mis-files an MA library URI as a track."""
        import inspect

        from services.execution.handlers import audiobookshelf as ab

        src = inspect.getsource(ab._dispatch_stream)
        ma_branch = src.split("if is_ma:")[1]
        assert '"media_type"' not in ma_branch
        # Roku's own ECP path legitimately still uses media_type; scope the guard
        # to _dispatch_stream so that path is not flagged.
        assert '"media_type": "track"' in inspect.getsource(ab._roku_play_audiobook)

    def test_ma_lookup_is_the_single_shared_resolver(self):
        """No second ABS->MA mapping may creep in beside the shared resolver."""
        from services.execution.handlers import audiobookshelf as ab

        source = pathlib.Path(ab.__file__).read_text()
        assert "ma_library.resolve_audiobook_uri" in source
        # The old broken shape must be gone from the play path.
        assert "audiobookshelf://" not in source

class TestPodcastDispatch:
    """A podcast episode on an MA player must resolve the *podcast*, not the episode.

    Measured live: `music/search` returns 0 results for any episode title, but the
    podcast's own title returns `library://podcast/N` whose provider_mappings
    carries the ABS podcast item id. Searching the episode title therefore always
    missed and reported "not in library" for a podcast MA actually has.
    """

    POD_ID = "ff781fed-af45-48e2-a62e-d3a91056afc5"
    EP_ID = "fef3ddd5-0a12-44bc-8f0e-9484d537be49"
    EP_TITLE = "2026 New Christendom Press Conference Recap"
    POD_TITLE = "The King's Hall"

    PODCAST_ITEM = {
        "id": POD_ID,
        "media": {
            "metadata": {"title": POD_TITLE},
            "episodes": [
                {"id": EP_ID, "title": EP_TITLE, "publishedAt": 2000},
                {"id": "ep-old", "title": "An Older Episode", "publishedAt": 1000},
            ],
        },
    }

    def _stack(self, state=MA_STATE, resolver_error=None):
        resolver = (
            AsyncMock(return_value=RESOLVED) if resolver_error is None
            else AsyncMock(side_effect=resolver_error)
        )
        return {
            "get_book": patch(
                "services.execution.abs_client.get_book",
                new=AsyncMock(return_value=self.PODCAST_ITEM),
            ),
            "session": patch(
                "services.execution.abs_client.start_playback_session",
                new=AsyncMock(return_value={"id": "sess-pod"}),
            ),
            "state": patch("services.execution.ha_client.get_state", new=AsyncMock(return_value=state)),
            "roku": patch(
                "services.execution.handlers.roku.is_roku_device", new=AsyncMock(return_value=False)
            ),
            "call": patch(
                "services.execution.ha_client.call_service", new=AsyncMock(return_value={"ok": True})
            ),
            "resolve": patch(
                "services.execution.handlers.audiobookshelf.ma_library.resolve_audiobook_uri",
                new=resolver,
            ),
            "interval": patch(
                "services.execution.handlers.audiobookshelf.PLAYBACK_VERIFY_INTERVAL", 0
            ),
        }

    def _req(self):
        return AudiobookshelfRequest(
            action="play_podcast_episode",
            book_id=self.POD_ID,
            episode_id=self.EP_ID,
            entity_id="media_player.tv",
            user_context=_ctx(),
        )

    @pytest.mark.asyncio
    async def test_resolves_the_podcast_by_its_abs_id(self):
        from contextlib import ExitStack

        with ExitStack() as st:
            mocks = {n: st.enter_context(c) for n, c in self._stack().items()}
            result = await handle_audiobookshelf(self._req())

        assert result.status == "SUCCESS", result.message
        resolver = mocks["resolve"]
        resolver.assert_awaited_once()
        args = resolver.await_args.args
        assert args[2] == self.POD_ID, "must resolve by the podcast item id"

    @pytest.mark.asyncio
    async def test_searches_the_podcast_title_not_the_episode_title(self):
        from contextlib import ExitStack

        with ExitStack() as st:
            mocks = {n: st.enter_context(c) for n, c in self._stack().items()}
            await handle_audiobookshelf(self._req())
        # An episode title matches nothing in MA; this was the actual bug.
        assert mocks["resolve"].await_args.args[3] == self.POD_TITLE

    @pytest.mark.asyncio
    async def test_ma_receives_the_resolved_uri(self):
        from contextlib import ExitStack

        with ExitStack() as st:
            mocks = {n: st.enter_context(c) for n, c in self._stack().items()}
            await handle_audiobookshelf(self._req())
        call = mocks["call"].call_args
        assert call.args[2] == "music_assistant"
        assert call.args[5]["media_id"] == "library://audiobook/260"
        assert "audiobookshelf://" not in str(call.args[5])

    @pytest.mark.asyncio
    async def test_non_ma_player_still_gets_the_hls_url(self):
        from contextlib import ExitStack

        plain = {"state": "playing", "attributes": {"media_title": "Ep"}}
        with ExitStack() as st:
            mocks = {n: st.enter_context(c) for n, c in self._stack(state=plain).items()}
            result = await handle_audiobookshelf(self._req())

        assert result.status == "SUCCESS", result.message
        data = mocks["call"].call_args.args[5]
        assert "abs-session/sess-pod/0" in data["media_content_id"]
        mocks["resolve"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unresolvable_podcast_fails_loudly(self):
        from contextlib import ExitStack
        from services.shared.ma_library import MALibraryLookupError

        err = MALibraryLookupError(
            f"Music Assistant does not have Audiobookshelf item {self.POD_ID}",
            reason="not_in_ma_library",
        )
        with ExitStack() as st:
            mocks = {n: st.enter_context(c) for n, c in self._stack(resolver_error=err).items()}
            result = await handle_audiobookshelf(self._req())

        assert result.status == "FAILURE"
        assert self.POD_ID in result.message
        mocks["call"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_requested_episode_is_the_one_started(self):
        from contextlib import ExitStack

        with ExitStack() as st:
            mocks = {n: st.enter_context(c) for n, c in self._stack().items()}
            await handle_audiobookshelf(self._req())
        # The episode id reaches ABS for the session...
        assert mocks["session"].await_args.args[3] == self.EP_ID
