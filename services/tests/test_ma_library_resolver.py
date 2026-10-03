"""Tests for services/shared/ma_library.py — the ABS item -> MA library URI resolver.

The bug this guards: Music Assistant is not a URL player. Handed an
``audiobookshelf://`` id or a stream URL it stores that text as the track *title*
and leaves the player idle, so audiobooks "played" silently. MA only accepts
``library://audiobook/<n>`` where ``<n>`` is MA-internal and obtainable only from
MA's own ``music/search`` results (``provider_mappings``), which the Home
Assistant service surface strips.

A fake WebSocket stands in for MA so the tests drive the real read/parse/match
logic, including the failures that must stay loud.
"""
import asyncio
import json
import uuid

import pytest

from services.shared import ma_library
from services.shared.ma_library import MALibraryLookupError

NARNIA_ABS_ID = "08d24fad-32a7-422d-8374-8501fdbe55d5"
MASS_URL = "http://ma.local:8095"
MASS_TOKEN = "test-mass-token"


class MAError(Exception):
    """Marker: this reply comes back as an MA error frame, not a result."""


class FakeWS:
    """Minimal websockets client stand-in that replies to commands from a table."""

    def __init__(self, replies: dict[str, object], *, notifications: int = 0):
        # command -> result, or command -> Exception to raise as an error frame
        self.replies = replies
        self.sent: list[dict] = []
        self.closed = False
        self._notifications = notifications

    async def send(self, raw: str) -> None:
        frame = json.loads(raw)
        self.sent.append(frame)
        cmd = frame.get("command")
        reply = self.replies.get(cmd)
        if isinstance(reply, Exception):
            self._pending = {
                "message_id": frame["message_id"],
                "error_code": 999,
                "error": str(reply),
            }
        else:
            self._pending = {"message_id": frame["message_id"], "result": reply}
        self._extra_notifications = self._notifications

    async def recv(self) -> str:
        # Server-initiated frames (server_info, player_state, …) carry no
        # message_id and must be skipped rather than mistaken for the reply.
        if self._extra_notifications:
            self._extra_notifications -= 1
            return json.dumps({"event": "player_state", "data": {"state": "idle"}})
        pending, self._pending = getattr(self, "_pending", None), None
        return json.dumps(pending)

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()
        return False


def _search_result(*, abs_id: str = NARNIA_ABS_ID, uri: str = "library://audiobook/260",
                   available=True, name="The Chronicles of Narnia") -> dict:
    return {
        "audiobooks": [
            {
                "item_id": uri.rsplit("/", 1)[-1],
                "provider": "library",
                "media_type": "audiobook",
                "uri": uri,
                "name": name,
                "provider_mappings": [
                    {
                        "item_id": abs_id,
                        "provider_domain": "audiobookshelf",
                        "provider_instance": "audiobookshelf--EpUcRzwM",
                        "available": available,
                        "in_library": True,
                    }
                ],
            }
        ]
    }


class _ConnectHandle:
    """Stands in for websockets' connect object: usable as ``async with``."""

    def __init__(self, factory):
        self._factory = factory

    def __await__(self):
        return self._factory().__await__()

    async def __aenter__(self):
        return await self._factory()

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def fake_connect(monkeypatch):
    """Patch the websockets connect used by ma_library; return a recorder."""
    import websockets.asyncio.client as ws_client

    opened: list[FakeWS] = []
    connect_kwargs: list[dict] = []

    def _install(replies: dict[str, object], *, notifications: int = 0,
                 raise_on_connect=None, fail_times: int = 0):
        attempts: list[int] = []

        async def _open():
            if raise_on_connect is not None:
                raise raise_on_connect
            attempts.append(1)
            if fail_times and len(attempts) <= fail_times:
                raise OSError("connection refused")
            ws = FakeWS(replies, notifications=notifications)
            opened.append(ws)
            return ws

        def _connect(url, **kwargs):
            connect_kwargs.append({"url": url, **kwargs})
            return _ConnectHandle(_open)

        _install.attempts = attempts
        monkeypatch.setattr(ws_client, "connect", _connect)
        return opened, connect_kwargs

    return _install


def _resolve(**kwargs):
    params = {
        "mass_url": MASS_URL,
        "mass_token": MASS_TOKEN,
        "abs_item_id": NARNIA_ABS_ID,
        "title": "The Chronicles of Narnia",
    }
    params.update(kwargs)
    return ma_library.resolve_audiobook_uri(**params)


class TestHappyPath:
    async def test_maps_abs_id_to_ma_library_uri(self, fake_connect):
        fake_connect({"auth": {"authenticated": True}, "music/search": _search_result()})
        resolved = await _resolve()
        assert resolved.ma_uri == "library://audiobook/260"
        assert resolved.abs_item_id == NARNIA_ABS_ID
        assert resolved.title == "The Chronicles of Narnia"

    async def test_authenticates_before_searching(self, fake_connect):
        opened, _ = fake_connect({"auth": {"authenticated": True}, "music/search": _search_result()})
        await _resolve()
        commands = [f["command"] for f in opened[0].sent]
        # MA rejects any command before auth with error_code 20.
        assert commands[0] == "auth"
        assert commands[1] == "music/search"

    async def test_auth_frame_carries_the_token(self, fake_connect):
        opened, _ = fake_connect({"auth": {"authenticated": True}, "music/search": _search_result()})
        await _resolve()
        auth_frame = opened[0].sent[0]
        assert auth_frame["args"] == {"token": MASS_TOKEN}
        assert auth_frame["message_id"]

    async def test_search_query_is_the_title(self, fake_connect):
        opened, _ = fake_connect({"auth": {"authenticated": True}, "music/search": _search_result()})
        await _resolve(title="Narnia")
        search = opened[0].sent[1]
        # MA requires `search_query`; `search` is rejected as an unknown arg.
        assert search["args"]["search_query"] == "Narnia"
        assert search["args"]["config"] == {"providers": ["library"]}

    async def test_empty_title_falls_back_to_the_item_id_as_query(self, fake_connect):
        opened, _ = fake_connect({"auth": {"authenticated": True}, "music/search": _search_result()})
        await _resolve(title="")
        # A blank search_query matches nothing in MA, so the id is the query.
        assert opened[0].sent[1]["args"]["search_query"] == NARNIA_ABS_ID

    async def test_ignores_server_notifications_while_waiting(self, fake_connect):
        # MA interleaves player_state/event frames; treating one as the reply
        # would hang or return the wrong thing.
        fake_connect(
            {"auth": {"authenticated": True}, "music/search": _search_result()},
            notifications=3,
        )
        assert (await _resolve()).ma_uri == "library://audiobook/260"

    async def test_picks_the_right_item_out_of_several(self, fake_connect):
        other = _search_result(abs_id="some-other-abs-id", uri="library://audiobook/999", name="Other Book")
        result = {
            "audiobooks": [
                other["audiobooks"][0],
                _search_result()["audiobooks"][0],
            ]
        }
        fake_connect({"auth": {"authenticated": True}, "music/search": result})
        resolved = await _resolve()
        assert resolved.ma_uri == "library://audiobook/260"
        assert resolved.title == "The Chronicles of Narnia"

    async def test_ignores_unavailable_candidate_and_takes_an_available_one(self, fake_connect):
        dead = _search_result(abs_id=NARNIA_ABS_ID, uri="library://audiobook/1", available=False)
        live = _search_result(abs_id=NARNIA_ABS_ID, uri="library://audiobook/2")
        result = {"audiobooks": [dead["audiobooks"][0], live["audiobooks"][0]]}
        fake_connect({"auth": {"authenticated": True}, "music/search": result})
        # Never hand MA a URI for media it says it cannot fetch.
        assert (await _resolve()).ma_uri == "library://audiobook/2"

    async def test_http_url_becomes_a_ws_url(self, fake_connect):
        _, connect_kwargs = fake_connect({"auth": {"authenticated": True}, "music/search": _search_result()})
        await _resolve(mass_url="http://ma.local:8095/")
        assert connect_kwargs[0]["url"].startswith("ws://ma.local:8095/ws?")

    async def test_https_url_becomes_a_wss_url(self, fake_connect):
        _, connect_kwargs = fake_connect({"auth": {"authenticated": True}, "music/search": _search_result()})
        await _resolve(mass_url="https://ma.example.com/")
        assert connect_kwargs[0]["url"].startswith("wss://ma.example.com/ws?")

    async def test_uses_keepalive_pings(self, fake_connect):
        # MA hangs up on a client that goes quiet; ping_interval=None fails.
        _, connect_kwargs = fake_connect({"auth": {"authenticated": True}, "music/search": _search_result()})
        await _resolve()
        assert connect_kwargs[0]["ping_interval"] == ma_library.WS_PING_INTERVAL
        assert connect_kwargs[0]["ping_timeout"] == ma_library.WS_PING_TIMEOUT


class TestFailuresAreLoud:
    """A miss must never be papered over with a guessed or raw-URL fallback."""

    async def test_unknown_abs_id_raises(self, fake_connect):
        fake_connect({"auth": {"authenticated": True}, "music/search": {"audiobooks": []}})
        with pytest.raises(MALibraryLookupError) as exc:
            await _resolve()
        assert exc.value.reason == "not_in_ma_library"
        assert NARNIA_ABS_ID in str(exc.value)

    async def test_item_in_another_provider_is_not_a_match(self, fake_connect):
        result = {
            "audiobooks": [
                {
                    "uri": "library://audiobook/5",
                    "name": "Something",
                    "provider_mappings": [
                        {"item_id": NARNIA_ABS_ID, "provider_domain": "opensubsonic", "available": True}
                    ],
                }
            ]
        }
        fake_connect({"auth": {"authenticated": True}, "music/search": result})
        with pytest.raises(MALibraryLookupError):
            await _resolve()

    async def test_ma_error_frame_is_surfaced_not_swallowed(self, fake_connect):
        fake_connect(
            {
                "auth": {"authenticated": True},
                "music/search": MAError("Invalid or unsupported command."),
            }
        )
        with pytest.raises(MALibraryLookupError) as exc:
            await _resolve()
        assert exc.value.reason == "ma_error_999"
        assert "Invalid or unsupported command." in str(exc.value)

    async def test_auth_failure_is_surfaced(self, fake_connect):
        fake_connect(
            {
                "auth": MAError("Authentication is required."),
                "music/search": _search_result(),
            }
        )
        with pytest.raises(MALibraryLookupError) as exc:
            await _resolve()
        assert exc.value.reason == "ma_error_999"

    async def test_non_library_uri_is_refused(self, fake_connect):
        # A raw URL coming back means MA did not resolve a library item.
        fake_connect(
            {"auth": {"authenticated": True}, "music/search": _search_result(uri="http://abs.local/x.m3u8")}
        )
        with pytest.raises(MALibraryLookupError) as exc:
            await _resolve()
        assert exc.value.reason == "unexpected_uri"

    async def test_item_without_uri_is_refused(self, fake_connect):
        result = {"audiobooks": [{"name": "x", "provider_mappings": [
            {"item_id": NARNIA_ABS_ID, "provider_domain": "audiobookshelf", "available": True}]}]}
        fake_connect({"auth": {"authenticated": True}, "music/search": result})
        with pytest.raises(MALibraryLookupError) as exc:
            await _resolve()
        assert exc.value.reason == "unexpected_uri"

    async def test_missing_token_fails_before_any_network_call(self, fake_connect):
        opened, connect_kwargs = fake_connect({"auth": {}, "music/search": _search_result()})
        with pytest.raises(MALibraryLookupError) as exc:
            await _resolve(mass_token="")
        assert exc.value.reason == "no_mass_token"
        assert connect_kwargs == []

    async def test_missing_url_fails_before_any_network_call(self, fake_connect):
        _, connect_kwargs = fake_connect({"auth": {}, "music/search": _search_result()})
        with pytest.raises(MALibraryLookupError) as exc:
            await _resolve(mass_url="")
        assert exc.value.reason == "no_mass_url"
        assert connect_kwargs == []

    async def test_non_http_url_is_refused(self, fake_connect):
        with pytest.raises(MALibraryLookupError) as exc:
            await _resolve(mass_url="ftp://ma.local")
        assert exc.value.reason == "bad_mass_url"

    async def test_missing_item_id_fails_unless_idless_allowed(self, fake_connect):
        with pytest.raises(MALibraryLookupError) as exc:
            await _resolve(abs_item_id="")
        assert exc.value.reason == "no_abs_item_id"

    async def test_no_title_and_no_id_fails(self, fake_connect):
        # Idless is allowed (podcast case) but there is still nothing to search
        # for, so it must not send MA an empty search_query.
        with pytest.raises(MALibraryLookupError) as exc:
            await ma_library.resolve_audiobook_uri(
                MASS_URL, MASS_TOKEN, "", "", allow_idless=True
            )
        assert exc.value.reason == "no_search_query"

    async def test_unreachable_ma_is_reported_as_unreachable(self, fake_connect, monkeypatch):
        fake_connect({}, raise_on_connect=OSError("connection refused"))
        monkeypatch.setattr(ma_library, "CONNECT_RETRY_DELAY", 0)
        with pytest.raises(MALibraryLookupError) as exc:
            await _resolve()
        assert exc.value.reason == "mass_unreachable"
        assert "connection refused" in str(exc.value)

    async def test_unreachable_ma_is_retried_once(self, fake_connect, monkeypatch):
        monkeypatch.setattr(ma_library, "CONNECT_RETRY_DELAY", 0)
        fake_connect(
            {"auth": {"authenticated": True}, "music/search": _search_result()},
            fail_times=1,
        )
        resolved = await _resolve()
        assert resolved.ma_uri == "library://audiobook/260"
        assert len(fake_connect.attempts) == 2

    async def test_retry_gives_up_after_the_configured_attempts(self, fake_connect, monkeypatch):
        monkeypatch.setattr(ma_library, "CONNECT_RETRY_DELAY", 0)
        fake_connect({}, fail_times=99)
        with pytest.raises(MALibraryLookupError) as exc:
            await _resolve()
        assert exc.value.reason == "mass_unreachable"
        assert len(fake_connect.attempts) == ma_library.CONNECT_ATTEMPTS

    async def test_a_lookup_miss_is_not_retried(self, fake_connect):
        # Retrying a miss cannot help and doubles the latency of a real failure.
        _, connect_kwargs = fake_connect({"auth": {"authenticated": True}, "music/search": {"audiobooks": []}})
        with pytest.raises(MALibraryLookupError):
            await _resolve()
        assert len(connect_kwargs) == 1


class TestIdlessLookup:
    """Podcast episodes have no ABS library item id to map from."""

    async def test_takes_first_available_audiobookshelf_item(self, fake_connect):
        result = _search_result(abs_id="whatever", uri="library://podcast_episode/77", name="Ep 1")
        result["podcast_episodes"] = result.pop("audiobooks")
        fake_connect({"auth": {"authenticated": True}, "music/search": result})
        resolved = await ma_library.resolve_audiobook_uri(
            MASS_URL, MASS_TOKEN, "", "Culture Apothecary", limit=5, allow_idless=True
        )
        assert resolved.ma_uri == "library://podcast_episode/77"

    async def test_skips_items_ma_cannot_fetch(self, fake_connect):
        dead = _search_result(uri="library://podcast_episode/1", available=False)
        live = _search_result(uri="library://podcast_episode/2")
        dead["podcast_episodes"] = dead.pop("audiobooks")
        live["podcast_episodes"] = live.pop("audiobooks")
        fake_connect({"auth": {"authenticated": True}, "music/search": {"podcast_episodes": [
            dead["podcast_episodes"][0], live["podcast_episodes"][0]]}})
        resolved = await ma_library.resolve_audiobook_uri(
            MASS_URL, MASS_TOKEN, "", "Culture Apothecary", limit=5, allow_idless=True
        )
        assert resolved.ma_uri == "library://podcast_episode/2"

    async def test_no_results_fails(self, fake_connect):
        fake_connect({"auth": {"authenticated": True}, "music/search": {}})
        with pytest.raises(MALibraryLookupError) as exc:
            await ma_library.resolve_audiobook_uri(
                MASS_URL, MASS_TOKEN, "", "Nothing", allow_idless=True
            )
        assert exc.value.reason == "not_in_ma_library"


class TestPayloadWalking:
    """MA groups results by media type; the matcher must not assume a shape."""

    def test_flattens_every_list_group(self):
        items = ma_library._iter_candidates(
            {"audiobooks": [{"uri": "a"}], "tracks": [{"uri": "b"}], "albums": []}
        )
        assert {i["uri"] for i in items} == {"a", "b"}

    def test_nested_single_object_is_included(self):
        assert ma_library._iter_candidates({"item": {"uri": "x"}})[0]["uri"] == "x"

    def test_non_dict_payload_yields_nothing(self):
        assert ma_library._iter_candidates(None) == []
        assert ma_library._iter_candidates([1, 2]) == []

    def test_match_ignores_non_list_provider_mappings(self):
        assert ma_library._match_by_abs_id([{"uri": "u", "provider_mappings": "nope"}], NARNIA_ABS_ID) is None

    def test_match_ignores_non_dict_mapping_entries(self):
        item = {"uri": "u", "provider_mappings": ["nope"]}
        assert ma_library._match_by_abs_id([item], NARNIA_ABS_ID) is None

    def test_item_id_comparison_is_case_insensitive(self):
        item = {"uri": "library://audiobook/1", "provider_mappings": [
            {"item_id": NARNIA_ABS_ID.upper(), "provider_domain": "audiobookshelf", "available": True}]}
        assert ma_library._match_by_abs_id([item], NARNIA_ABS_ID) is not None


class TestFirstSegmentUri:
    """The gateway waits for segment 0; this picks the right line."""

    M3U8 = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:6\n"
        "#EXTINF:6,\n"
        "output-0.ts\n"
        "#EXTINF:6,\n"
        "output-1.ts\n"
    )

    def test_picks_first_media_line(self):
        assert ma_library and True  # module imported for clarity
        from services.gateway.main import _first_segment_uri

        assert _first_segment_uri(self.M3U8) == "output-0.ts"

    def test_skips_comments_and_blanks(self):
        from services.gateway.main import _first_segment_uri

        assert _first_segment_uri("#EXTM3U\n\n#EXTINF:6,\n\nseg.ts\n") == "seg.ts"

    def test_returns_none_when_only_comments(self):
        from services.gateway.main import _first_segment_uri

        assert _first_segment_uri("#EXTM3U\n#EXT-X-ENDLIST\n") is None

    def test_returns_none_for_empty(self):
        from services.gateway.main import _first_segment_uri

        assert _first_segment_uri("") is None