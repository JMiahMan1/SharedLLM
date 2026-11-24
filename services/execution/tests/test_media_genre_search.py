"""A genre request searches playlists and radio first, and tries its relaxed words once.

"worship music" found nothing; "worship" as a track title found "Call to Worship". A genre is
a playlist or a station, so those come first. Values are made up.
"""
import asyncio
from unittest.mock import AsyncMock

from services.execution.handlers import media as media_handler
from services.execution.schemas import MediaPlayRequest, UserContext

P = "services.execution.handlers."


class _MA:
    """Answers music_assistant.search from ``replies`` (query -> response) and records every call."""

    def __init__(self, replies):
        self.replies = replies
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, ha_url, ha_token, domain, service, entity_id="", service_data=None, return_response=False):
        self.calls.append((service, service_data))
        if service == "search":
            return {"ok": True, "service_response": self.replies.get(service_data["name"], {})}
        return {"ok": True}

    def searched(self):
        return [d["name"] for s, d in self.calls if s == "search"]

    def played(self):
        return [d["media_id"] for s, d in self.calls if s == "play_media"]


def _play(monkeypatch, ma, **req):
    monkeypatch.setattr(media_handler, "MASS_CONFIG_ENTRY_ID", "ce-test")
    monkeypatch.setattr(media_handler, "resolve_mass_entity", AsyncMock(return_value="media_player.den"))
    monkeypatch.setattr(media_handler, "_corrected_music_query", AsyncMock(return_value=None))
    monkeypatch.setattr(f"{P}roku.is_roku_device", AsyncMock(return_value=False))
    monkeypatch.setattr(f"{P}samsung.is_samsung_tv", AsyncMock(return_value=False))
    monkeypatch.setattr(media_handler.ha_client, "call_service", ma)
    ctx = UserContext(user="sam", ha_url="http://hub.example.test:8123", ha_token="hub")
    r = MediaPlayRequest(user_context=ctx, entity_id="media_player.den", enqueue="replace", **req)
    return asyncio.run(media_handler._play_music_via_ha(r, "media_player.den", ctx))


MIXED = {
    "tracks": [{"uri": "library://track/1", "name": "Call to Fake"}],
    "playlists": [{"uri": "library://playlist/7", "name": "Fake Worship Mix"}],
}


def test_a_genre_prefers_a_playlist_over_a_track_title(monkeypatch):
    ma = _MA({"worship": MIXED})
    assert _play(monkeypatch, ma, query="worship", media_content_type="genre").status == "SUCCESS"
    assert ma.played() == ["library://playlist/7"]
    assert ma.calls[0][1]["media_type"][:2] == ["playlist", "radio"]


def test_an_artist_request_keeps_the_track_first_order(monkeypatch):
    ma = _MA({"Example Band": MIXED})
    assert _play(monkeypatch, ma, query="Example Band", media_content_type="artist").status == "SUCCESS"
    assert ma.played() == ["library://track/1"]


def test_the_relaxed_words_are_tried_once_when_the_first_search_finds_nothing(monkeypatch):
    ma = _MA({"worship": MIXED})
    result = _play(monkeypatch, ma, query="worship music", fallback_query="worship", media_content_type="genre")
    assert result.status == "SUCCESS"
    assert ma.searched() == ["worship music", "worship"]
    assert ma.played() == ["library://playlist/7"]


def test_nothing_found_either_way_fails_after_one_relaxed_try(monkeypatch):
    ma = _MA({})
    result = _play(monkeypatch, ma, query="worship music", fallback_query="worship", media_content_type="genre")
    assert result.status == "FAILURE"
    assert ma.searched() == ["worship music", "worship"]
