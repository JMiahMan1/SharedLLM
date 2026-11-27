"""Media asked for at a voice satellite: a misheard name, "play music", audiobooks by author.

Every name here is made up (artists, authors, titles, players).
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from services.execution import media_vocabulary, wyoming_stt
from services.execution.handlers import audiobookshelf, mass_ha_client
from services.execution.handlers import media as media_handler
from services.execution.schemas import AudiobookshelfRequest, MediaPlayRequest, UserContext

HA_URL = "http://ha.example.test:8123"
SPEAKER = "media_player.den_satellite_speaker"


@pytest.fixture(autouse=True)
def _fresh_vocabulary():
    media_vocabulary.reset()
    yield
    media_vocabulary.reset()


def _ctx():
    return UserContext(user="sam", ha_url=HA_URL, ha_token="tok")


# --------------------------------------------------------------------------- the vocabulary


@pytest.mark.parametrize("heard, names, meant", [
    ("brand in Lake", ["Brandon Lake", "Lake Street Fakes", "Brand New Example"], "Brandon Lake"),
    ("Ginny B. Jones", ["Jenny B. Jones", "Jonas Fakeman"], "Jenny B. Jones"),
    ("Jenny B Jones", ["Jenny B. Jones"], "Jenny B. Jones"),
    ("completely different", ["Brandon Lake", "Jenny B. Jones"], None),
    ("ab", ["Abba Fake"], None),
])
def test_a_misheard_name_meets_the_library_name(heard, names, meant):
    assert media_vocabulary.best_match(heard, names) == meant


def test_two_equally_close_names_are_not_guessed():
    assert media_vocabulary.best_match("Ann Fakes", ["Ann Fake", "Anne Fakes"]) is None
    assert media_vocabulary.best_match("Example Band", ["Example Bend", "Example Bond"]) is None


def test_the_hint_list_is_bounded():
    media_vocabulary.remember("artist", [f"Made Up Artist Number {n}" for n in range(300)])
    media_vocabulary.remember("author", [f"Made Up Author {n}" for n in range(300)])
    hints = media_vocabulary.hints()
    assert hints and len(hints) <= media_vocabulary.MAX_HINT_NAMES
    assert sum(len(h) + 2 for h in hints) <= media_vocabulary.MAX_HINT_CHARS
    assert any("Artist" in h for h in hints) and any("Author" in h for h in hints)  # every kind gets a turn
    assert len(media_vocabulary.names("artist")) == 300
    media_vocabulary.remember("artist", [f"More Artist {n}" for n in range(media_vocabulary.MAX_PER_KIND)])
    assert len(media_vocabulary.names("artist")) == media_vocabulary.MAX_PER_KIND
    assert len(media_vocabulary.initial_prompt()) <= media_vocabulary.MAX_HINT_CHARS + 1


def test_long_or_empty_names_are_not_hints():
    media_vocabulary.remember("title", ["", "   ", "x" * 200, "A Fine Fake Title"])
    assert media_vocabulary.names("title") == ["A Fine Fake Title"]
    with pytest.raises(ValueError):
        media_vocabulary.remember("planet", ["Mars"])


def test_speech_to_text_is_offered_the_names_with_each_command():
    media_vocabulary.remember("artist", ["Brandon Lake"])
    media_vocabulary.remember("author", ["Jenny B. Jones"])
    header = {"type": "transcribe", "data": {"language": "en"}}
    raw = (json.dumps(header) + "\n").encode()
    out = wyoming_stt.with_name_hints(wyoming_stt.Event(header, dict(header["data"]), b"", raw))
    sent = json.loads(out.decode().splitlines()[0])
    assert sent["type"] == "transcribe" and sent["data"]["language"] == "en"
    prompt = sent["data"]["context"][wyoming_stt.HINTS_CONTEXT_KEY]
    assert "Brandon Lake" in prompt and "Jenny B. Jones" in prompt
    chunk = wyoming_stt.Event({"type": "audio-chunk"}, {}, b"\x00\x01", b"raw-bytes")
    assert wyoming_stt.with_name_hints(chunk) == b"raw-bytes"  # audio passes through untouched


def test_with_no_names_learnt_the_command_passes_through_unchanged():
    header = {"type": "transcribe", "data": {"language": "en"}}
    raw = (json.dumps(header) + "\n").encode()
    assert wyoming_stt.with_name_hints(wyoming_stt.Event(header, dict(header["data"]), b"", raw)) == raw


# --------------------------------------------------------------------------- music


class _Calls:
    def __init__(self, responses):
        self.calls: list[tuple[str, str, str, dict]] = []
        self.responses = responses

    async def __call__(self, ha_url, ha_token, domain, service, entity_id="", service_data=None, return_response=False):
        self.calls.append((domain, service, entity_id, service_data or {}))
        answer = self.responses.get(service, {"ok": True})
        return answer(service_data or {}) if callable(answer) else answer

    def of(self, service):
        return [c for c in self.calls if c[1] == service]


def _search_finding(name, uri):
    def answer(data):
        found = data.get("name") == name
        return {"ok": True, "service_response": {"service_response": {
            "artists": [{"uri": uri, "name": name}] if found else [], "tracks": [], "albums": [],
            "playlists": [], "radio": []}}}
    return answer


_real_resolve = media_handler.resolve_mass_entity


def _patch_music(monkeypatch, calls, library=None, default=""):
    monkeypatch.setattr(media_handler, "MASS_CONFIG_ENTRY_ID", "entry-1")
    monkeypatch.setattr(media_handler, "resolve_mass_entity", AsyncMock(return_value="media_player.ma_den_satellite"))
    monkeypatch.setattr("services.execution.handlers.roku.is_roku_device", AsyncMock(return_value=False))
    monkeypatch.setattr("services.execution.handlers.samsung.is_samsung_tv", AsyncMock(return_value=False))
    monkeypatch.setattr(media_handler.ha_client, "call_service", calls)
    monkeypatch.setattr(media_handler, "_global_setting", AsyncMock(return_value=default))
    lib = library or {}

    async def get_library(ha_url, ha_token, media_type, limit=50, **kw):
        return list(lib.get(mass_ha_client._normalize_media_type(media_type), []))

    monkeypatch.setattr(mass_ha_client, "get_library", get_library)


def _music(query):
    return MediaPlayRequest(user_context=_ctx(), entity_id=SPEAKER, query=query, media_type="music")


async def test_a_misheard_artist_is_searched_again_as_the_library_name(monkeypatch):
    calls = _Calls({"search": _search_finding("Brandon Lake", "library://artist/7")})
    _patch_music(monkeypatch, calls, library={"artist": [{"name": "Brandon Lake", "uri": "library://artist/7"},
                                                         {"name": "Example Band", "uri": "library://artist/8"}]})
    result = await media_handler.play_music(_music("brand in Lake"), SPEAKER, _ctx())
    assert result.status == "SUCCESS"
    assert [c[3]["name"] for c in calls.of("search")] == ["brand in Lake", "Brandon Lake"]
    assert calls.of("play_media")[0][3]["media_id"] == "library://artist/7"


async def test_a_name_nothing_resembles_still_fails_and_plays_nothing(monkeypatch):
    calls = _Calls({"search": _search_finding("Brandon Lake", "library://artist/7")})
    _patch_music(monkeypatch, calls, library={"artist": [{"name": "Brandon Lake", "uri": "library://artist/7"}]})
    result = await media_handler.play_music(_music("quantum chromodynamics"), SPEAKER, _ctx())
    assert result.status == "FAILURE" and "No match" in result.message
    assert calls.of("play_media") == []


async def test_play_music_with_nothing_named_shuffles_a_library_playlist(monkeypatch):
    calls = _Calls({})
    _patch_music(monkeypatch, calls, library={"playlist": [{"name": "Made Up Mix", "uri": "library://playlist/3"}]})
    result = await media_handler.play_music(_music(None), SPEAKER, _ctx())
    assert result.status == "SUCCESS" and "Made Up Mix" in result.message
    assert calls.of("search") == []  # never a search for "Can you play music?"
    assert calls.of("play_media")[0][3]["media_id"] == "library://playlist/3"
    assert calls.of("shuffle_set")[0][3] == {"shuffle": True}


async def test_play_music_falls_back_to_library_tracks(monkeypatch):
    calls = _Calls({})
    tracks = [{"name": f"Fake Song {n}", "uri": f"library://track/{n}"} for n in range(40)]
    _patch_music(monkeypatch, calls, library={"track": tracks})
    result = await media_handler.play_music(_music(None), SPEAKER, _ctx())
    assert result.status == "SUCCESS"
    queued = calls.of("play_media")[0][3]["media_id"]
    assert isinstance(queued, list) and len(queued) == media_handler.SHUFFLE_TRACKS


async def test_play_music_uses_the_default_from_settings(monkeypatch):
    calls = _Calls({"search": _search_finding("Made Up Radio", "library://artist/9")})
    _patch_music(monkeypatch, calls, default="Made Up Radio")
    result = await media_handler.play_music(_music(None), SPEAKER, _ctx())
    assert result.status == "SUCCESS"
    assert calls.of("search")[0][3]["name"] == "Made Up Radio"


async def test_play_music_with_an_empty_library_says_what_to_do(monkeypatch):
    calls = _Calls({})
    _patch_music(monkeypatch, calls)
    result = await media_handler.play_music(_music(None), SPEAKER, _ctx())
    assert result.status == "FAILURE"
    assert "no music in the library" in result.message and media_handler.DEFAULT_MUSIC_SETTING in result.message
    assert calls.of("play_media") == []


async def test_a_satellite_speaker_is_not_swapped_for_the_rooms_tv(monkeypatch):
    """Music Assistant's player for the room's TV is the TV, not the satellite asked."""
    from services.execution.capability_index import CapabilityIndex

    def cap(eid, integration, device):
        return {"entity_id": eid, "domain": "media_player", "integration": integration, "platform": integration,
                "area_id": "den", "device_id": device, "available": True, "abilities": ["play_media"],
                "details": {}, "services": []}

    idx = CapabilityIndex([cap(SPEAKER, "esphome", "dev_sat"), cap("media_player.den_tv_ma", "music_assistant", "dev_ma"),
                           cap("media_player.den_satellite_speaker_ma", "music_assistant", "dev_ma2")],
                          {}, [{"area_id": "den", "name": "Den", "aliases": []}])
    states = [{"entity_id": SPEAKER, "attributes": {"friendly_name": "Den Satellite Speaker"}},
              {"entity_id": "media_player.den_tv_ma", "attributes": {"friendly_name": "Den TV"}},
              {"entity_id": "media_player.den_satellite_speaker_ma",
               "attributes": {"friendly_name": "Den Satellite Speaker"}}]
    monkeypatch.setattr(media_handler, "_routing_index", AsyncMock(return_value=idx))
    monkeypatch.setattr(media_handler.ha_client, "get_states", AsyncMock(return_value=states))
    ctx = type("C", (), {"ha_url": HA_URL, "ha_token": "t"})()
    assert await media_handler.resolve_mass_entity(ctx, SPEAKER) == "media_player.den_satellite_speaker_ma"
    monkeypatch.setattr(media_handler.ha_client, "get_states", AsyncMock(return_value=states[:2]))
    idx2 = CapabilityIndex([cap(SPEAKER, "esphome", "dev_sat"), cap("media_player.den_tv_ma", "music_assistant", "dev_ma")],
                           {}, [{"area_id": "den", "name": "Den", "aliases": []}])
    monkeypatch.setattr(media_handler, "_routing_index", AsyncMock(return_value=idx2))
    assert await media_handler.resolve_mass_entity(ctx, SPEAKER) == SPEAKER


SAT_SPEAKER = "media_player.office_satellite_media_player"
SAT_MA = "media_player.echo_dot_3_abc123"


def _sat_index(with_ma: bool):
    """The hub's view of a satellite that connected itself to Music Assistant over Sendspin.

    HomeHub puts MA's universal player (named after the node, no IP or MAC) on the satellite's
    ESPHome device; the hidden Sendspin protocol player gets no entity. A TV's MA player is in the room.
    """
    from services.execution.capability_index import CapabilityIndex

    def cap(eid, integration, device):
        return {"entity_id": eid, "domain": "media_player", "integration": integration, "platform": integration,
                "area_id": "office", "device_id": device, "available": True, "abilities": ["play_media"],
                "details": {}, "services": []}

    caps = [cap(SAT_SPEAKER, "esphome", "dev_sat"), cap("media_player.office_tv_ma", "music_assistant", "dev_tv")]
    if with_ma:
        caps.append(cap(SAT_MA, "music_assistant", "dev_sat"))
    states = [{"entity_id": SAT_SPEAKER, "state": "idle", "attributes": {"friendly_name": "Office Satellite"}},
              {"entity_id": "media_player.office_tv_ma", "state": "off",
               "attributes": {"friendly_name": "Office TV", "mass_player_type": "player", "active_queue": None}}]
    if with_ma:
        states.append({"entity_id": SAT_MA, "state": "idle",
                       "attributes": {"friendly_name": "Echo Dot 3 abc123", "mass_player_type": "player",
                                      "mass_player_id": "up0000000abc", "active_queue": None}})
    return CapabilityIndex(caps, {}, [{"area_id": "office", "name": "Office", "aliases": []}]), states


async def test_the_satellites_own_speaker_resolves_to_its_music_assistant_player(monkeypatch):
    idx, states = _sat_index(with_ma=True)
    monkeypatch.setattr(media_handler, "_routing_index", AsyncMock(return_value=idx))
    monkeypatch.setattr(media_handler.ha_client, "get_states", AsyncMock(return_value=states))
    ctx = type("C", (), {"ha_url": HA_URL, "ha_token": "t"})()
    # The names differ ("Office Satellite" vs MA's "Echo Dot 3 abc123"): the shared device is the link.
    assert await media_handler.resolve_mass_entity(ctx, SAT_SPEAKER) == SAT_MA


async def test_music_on_a_satellite_with_its_music_assistant_player_plays_there(monkeypatch):
    idx, states = _sat_index(with_ma=True)
    calls = _Calls({})
    _patch_music(monkeypatch, calls, library={"playlist": [{"name": "Made Up Mix", "uri": "library://playlist/3"}]})
    monkeypatch.setattr(media_handler, "resolve_mass_entity", _real_resolve)
    monkeypatch.setattr(media_handler, "_routing_index", AsyncMock(return_value=idx))
    monkeypatch.setattr(media_handler.ha_client, "get_states", AsyncMock(return_value=states))
    req = MediaPlayRequest(user_context=_ctx(), entity_id=SAT_SPEAKER, query=None, media_type="music")
    result = await media_handler.play_music(req, SAT_SPEAKER, _ctx())
    assert result.status == "SUCCESS"
    assert [c[2] for c in calls.of("play_media")] == [SAT_MA]


async def test_music_on_a_satellite_not_connected_to_music_assistant_says_so(monkeypatch):
    idx, states = _sat_index(with_ma=False)
    calls = _Calls({})
    _patch_music(monkeypatch, calls, library={"playlist": [{"name": "Made Up Mix", "uri": "library://playlist/3"}]})
    monkeypatch.setattr(media_handler, "resolve_mass_entity", _real_resolve)
    monkeypatch.setattr(media_handler, "_routing_index", AsyncMock(return_value=idx))
    monkeypatch.setattr(media_handler.ha_client, "get_states", AsyncMock(return_value=states))
    req = MediaPlayRequest(user_context=_ctx(), entity_id=SAT_SPEAKER, query=None, media_type="music")
    result = await media_handler.play_music(req, SAT_SPEAKER, _ctx())
    assert result.status == "FAILURE"
    assert result.message.startswith("Office Satellite is not connected to Music Assistant")
    assert calls.of("play_media") == [] and calls.of("search") == []  # never the TV, never a raw 400


# --------------------------------------------------------------------------- audiobooks


def _book(item_id, title, author, year, series=""):
    meta = {"title": title, "authorName": author, "publishedYear": str(year)}
    if series:
        meta["seriesName"] = series
    return {"id": item_id, "addedAt": year, "media": {"metadata": meta, "duration": 3600, "numAudioFiles": 1}}


LIBRARY = [
    _book("b1", "The First Fake", "Jenny B. Jones", 2015, "Fake Saga #1"),
    _book("b2", "The Newest Fake", "Jenny B. Jones", 2022, "Fake Saga #2"),
    _book("b3", "A Middle Fake", "Jenny B. Jones", 2019),
    _book("b4", "Unrelated Example", "Someone Else", 2024),
]


def _patch_abs(monkeypatch, progress=None):
    played: list[str] = []

    async def play(abs_url, abs_key, req, book_id):
        played.append(book_id)
        return audiobookshelf.ExecutionResult(status="SUCCESS", message=f"Now playing: {book_id}",
                                              service="audiobookshelf")

    monkeypatch.setattr(audiobookshelf.abs_client, "get_book_library_items", AsyncMock(return_value=list(LIBRARY)))
    monkeypatch.setattr(audiobookshelf, "_play_book_session", play)
    monkeypatch.setattr(audiobookshelf, "_all_progress_by_item", AsyncMock(return_value=progress or {}))
    return played


def _abs(action, **kw):
    return AudiobookshelfRequest(user_context=_ctx(), action=action, entity_id=SPEAKER, **kw)


async def test_the_latest_book_by_a_misheard_author(monkeypatch):
    played = _patch_abs(monkeypatch)
    result = await audiobookshelf._handle_play_by("http://abs.test", "k", _abs("play_by", author="Ginny B Jones",
                                                                               latest=True))
    assert result.status == "SUCCESS" and played == ["b2"]
    assert "Jenny B. Jones" in media_vocabulary.names("author")  # learnt for speech-to-text hints


async def test_a_book_by_an_author_prefers_the_one_in_progress(monkeypatch):
    played = _patch_abs(monkeypatch, progress={"b3": {"lastUpdate": 5, "isFinished": False},
                                               "b1": {"lastUpdate": 9, "isFinished": True}})
    await audiobookshelf._handle_play_by("http://abs.test", "k", _abs("play_by", author="Jenny B. Jones"))
    assert played == ["b3"]


async def test_a_book_in_a_series(monkeypatch):
    played = _patch_abs(monkeypatch)
    await audiobookshelf._handle_play_by("http://abs.test", "k", _abs("play_by", series="Fake Saga", latest=True))
    assert played == ["b2"]


async def test_an_author_the_library_does_not_have(monkeypatch):
    played = _patch_abs(monkeypatch)
    result = await audiobookshelf._handle_play_by("http://abs.test", "k", _abs("play_by", author="Nobody Atall"))
    assert result.status == "FAILURE" and "Nobody Atall" in result.message and played == []


async def test_an_author_asked_for_as_a_title_still_finds_their_book(monkeypatch):
    """'Could not find audiobook "Jenny B. Jones"': an author searched as a title, misheard as well."""
    played = _patch_abs(monkeypatch)
    monkeypatch.setattr(audiobookshelf.abs_client, "search_library", AsyncMock(return_value={"results": []}))
    result = await audiobookshelf._handle_play("http://abs.test", "k", _abs("play", query="Ginny B. Jones"))
    assert result.status == "SUCCESS" and played == ["b2"]


async def test_a_misheard_title_is_searched_as_the_library_title(monkeypatch):
    played = _patch_abs(monkeypatch)

    async def search(abs_url, abs_key, query, limit=10):
        return {"results": [b for b in LIBRARY if b["media"]["metadata"]["title"] == query]}

    monkeypatch.setattr(audiobookshelf.abs_client, "search_library", search)
    result = await audiobookshelf._handle_play("http://abs.test", "k", _abs("play", query="The Newest Fakes"))
    assert result.status == "SUCCESS" and played == ["b2"]
