"""An MA item picked in the app (a playlist, album, track) fills the chosen
speaker's own Music Assistant queue.

Samsung TVs used to take every direct URI to the TV's own player, which
cannot play a `library://` address and leaves MA's queue untouched; Roku
used to search MA for the URI text as if it were a name. Both now hand the
URI to the speaker's MA player with `enqueue=replace`.
"""
import asyncio
from unittest.mock import AsyncMock

from services.execution.handlers import media as media_handler
from services.execution.handlers import roku as roku_handler
from services.execution.schemas import MediaPlayRequest, UserContext

P_ROKU = "services.execution.handlers.roku."
P_SAMSUNG = "services.execution.handlers.samsung."

HA_URL = "http://ha.local:8123"
HA_TOKEN = "tok"
PLAYLIST = "library://playlist/5"


def _req(query: str, entity_id: str) -> MediaPlayRequest:
    return MediaPlayRequest(
        user_context=UserContext(user="queue", ha_url=HA_URL, ha_token=HA_TOKEN),
        entity_id=entity_id,
        query=query,
        enqueue="replace",
    )


class _CallRecorder:
    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, ha_url, ha_token, domain, service, entity_id="", service_data=None, return_response=False):
        self.calls.append({"domain": domain, "service": service, "entity_id": entity_id, "service_data": service_data})
        return {"ok": True}


def _patch_samsung(monkeypatch, recorder, *, ma_entity_state):
    monkeypatch.setattr(media_handler, "MASS_CONFIG_ENTRY_ID", "ce-test")
    monkeypatch.setattr(media_handler, "resolve_mass_entity", AsyncMock(return_value="media_player.loft_tv_2"))
    monkeypatch.setattr(f"{P_ROKU}is_roku_device", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{P_SAMSUNG}is_samsung_tv", AsyncMock(return_value=True))
    samsung_play = AsyncMock()
    monkeypatch.setattr(f"{P_SAMSUNG}play_music", samsung_play)
    monkeypatch.setattr(media_handler.ha_client, "get_state", AsyncMock(return_value=ma_entity_state))
    monkeypatch.setattr(media_handler.ha_client, "call_service", recorder)
    return samsung_play


def test_samsung_playlist_fills_the_tvs_ma_queue(monkeypatch):
    recorder = _CallRecorder()
    samsung_play = _patch_samsung(
        monkeypatch, recorder,
        ma_entity_state={"attributes": {"app_id": "music_assistant", "active_queue": "media_player.loft_tv"}},
    )
    result = asyncio.run(media_handler.play_music(_req(PLAYLIST, "media_player.loft_tv"), "media_player.loft_tv", _req(PLAYLIST, "x").user_context))

    assert result.status == "SUCCESS"
    samsung_play.assert_not_called()
    assert recorder.calls == [{
        "domain": "music_assistant", "service": "play_media", "entity_id": "media_player.loft_tv_2",
        "service_data": {"media_id": PLAYLIST, "enqueue": "replace"},
    }]


def test_samsung_web_address_still_goes_to_the_tv(monkeypatch):
    recorder = _CallRecorder()
    samsung_play = _patch_samsung(
        monkeypatch, recorder,
        ma_entity_state={"attributes": {"app_id": "music_assistant", "active_queue": "media_player.loft_tv"}},
    )
    samsung_play.return_value = media_handler.ExecutionResult(status="SUCCESS", message="ok", service="samsung")
    url = "https://example.test/song.mp3"
    asyncio.run(media_handler.play_music(_req(url, "media_player.loft_tv"), "media_player.loft_tv", _req(url, "x").user_context))

    samsung_play.assert_awaited_once()
    assert recorder.calls == []


def test_samsung_without_an_ma_player_keeps_the_tv_path(monkeypatch):
    recorder = _CallRecorder()
    samsung_play = _patch_samsung(monkeypatch, recorder, ma_entity_state={"attributes": {"friendly_name": "Loft TV"}})
    samsung_play.return_value = media_handler.ExecutionResult(status="SUCCESS", message="ok", service="samsung")
    asyncio.run(media_handler.play_music(_req(PLAYLIST, "media_player.loft_tv"), "media_player.loft_tv", _req(PLAYLIST, "x").user_context))

    samsung_play.assert_awaited_once()


def test_roku_plays_an_ma_playlist_as_given(monkeypatch):
    recorder = _CallRecorder()
    monkeypatch.setattr(roku_handler, "get_roku_ip", AsyncMock(return_value=None))
    monkeypatch.setattr(roku_handler, "find_ma_player_sibling", AsyncMock(return_value="media_player.gracies_tv"))
    monkeypatch.setattr(roku_handler.ha_client, "call_service", recorder)

    result = asyncio.run(roku_handler.roku_play_music(HA_URL, HA_TOKEN, "media_player.28_tcl_roku_tv", PLAYLIST, "ce-test"))

    assert result.status == "SUCCESS"
    assert [c["service"] for c in recorder.calls] == ["play_media"]
    assert recorder.calls[0]["service_data"] == {"media_id": PLAYLIST, "media_type": "playlist", "enqueue": "replace"}


def test_roku_with_ip_skips_the_name_search_for_a_uri(monkeypatch):
    recorder = _CallRecorder()
    monkeypatch.setattr(roku_handler, "get_roku_ip", AsyncMock(return_value="192.0.2.10"))
    monkeypatch.setattr(roku_handler, "find_ma_player_sibling", AsyncMock(return_value="media_player.gracies_tv"))
    monkeypatch.setattr(roku_handler.ha_client, "call_service", recorder)
    monkeypatch.setattr(roku_handler.asyncio, "sleep", AsyncMock())

    class _NoNetwork:
        def __init__(self, *a, **k):
            raise OSError("offline in tests")

    import aiohttp
    monkeypatch.setattr(aiohttp, "ClientSession", _NoNetwork)
    monkeypatch.setattr(roku_handler.device_registry, "invalidate_device", AsyncMock())

    result = asyncio.run(roku_handler.roku_play_music(HA_URL, HA_TOKEN, "media_player.gracies_tv", PLAYLIST, "ce-test"))

    assert result.status == "SUCCESS"
    assert [c["service"] for c in recorder.calls] == ["play_media"]
    assert recorder.calls[0]["service_data"] == {"media_id": PLAYLIST, "media_type": "playlist", "enqueue": "replace"}


def test_ma_uri_type():
    assert roku_handler._ma_uri_type("library://playlist/5") == "playlist"
    assert roku_handler._ma_uri_type("spotify://album/abc") == "album"
    assert roku_handler._ma_uri_type("https://example.test/a.mp3") is None
    assert roku_handler._ma_uri_type("worship songs") is None


def test_roku_add_to_queue_does_not_relaunch_the_channel(monkeypatch):
    recorder = _CallRecorder()
    monkeypatch.setattr(roku_handler, "get_roku_ip", AsyncMock(return_value="192.0.2.10"))
    monkeypatch.setattr(roku_handler, "find_ma_player_sibling", AsyncMock(return_value="media_player.gracies_tv"))
    monkeypatch.setattr(roku_handler.ha_client, "call_service", recorder)

    import aiohttp

    def _launch(*a, **k):
        raise AssertionError("queueing must not relaunch the Roku channel")

    monkeypatch.setattr(aiohttp, "ClientSession", _launch)

    result = asyncio.run(roku_handler.roku_play_music(HA_URL, HA_TOKEN, "media_player.gracies_tv", PLAYLIST, "ce-test", enqueue="add"))

    assert result.status == "SUCCESS"
    assert recorder.calls[0]["service_data"] == {"media_id": PLAYLIST, "media_type": "playlist", "enqueue": "add"}


def test_play_next_on_a_speaker_passes_the_queue_option(monkeypatch):
    recorder = _CallRecorder()
    _patch_samsung(
        monkeypatch, recorder,
        ma_entity_state={"attributes": {"app_id": "music_assistant", "active_queue": "media_player.loft_tv"}},
    )
    req = _req(PLAYLIST, "media_player.loft_tv")
    req.enqueue = "next"
    asyncio.run(media_handler.play_music(req, "media_player.loft_tv", req.user_context))

    assert recorder.calls[0]["service_data"] == {"media_id": PLAYLIST, "enqueue": "next"}
