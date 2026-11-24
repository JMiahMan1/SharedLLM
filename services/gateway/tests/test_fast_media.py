"""Spoken media requests from a voice satellite (fast_media.py and the chat handler's fast path).

Every name is made up: a satellite in a made-up den with its own speaker, the den's TV,
and made-up artists and authors.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from services.gateway import fast_media
from services.gateway import main as gateway_main
from services.gateway.intent_engine import IntentEngine

SAT = "assist_satellite.den_satellite"
SAT_SPEAKER = "media_player.den_satellite_speaker"
TV = "media_player.den_tv_cast"
KITCHEN = "media_player.kitchen_speaker"
SECRET = "made-up-internal-secret"


def _player(eid, name, state="idle"):
    return {"entity_id": eid, "state": state, "attributes": {"friendly_name": name}}


PLAYERS = [
    _player(SAT_SPEAKER, "Den Satellite Speaker"),
    _player(TV, "Den TV Chrome"),
    _player(KITCHEN, "Kitchen Speaker"),
]

VOICE = {"satellite": SAT, "media_player": SAT_SPEAKER, "area": "Den", "area_id": "den",
         "satellite_name": "Den Satellite"}


# --------------------------------------------------------------------------- the words


@pytest.mark.parametrize("query, cleaned", [
    ("Can you play music?", "play music"),
    ("Jarvis, could you please play some music for me", "play some music"),
    ("Please play Example Band, Jarvis.", "play Example Band"),
    ("I'd like you to play a song", "play a song"),
    # A device that may carry "Jarvis" in its name keeps it.
    ("Play brand in Lake on Office, Jarvis.", "Play brand in Lake on Office, Jarvis"),
])
def test_filler_around_a_request_is_dropped(query, cleaned):
    assert fast_media.strip_request_filler(query) == cleaned


@pytest.mark.parametrize("words, generic", [
    ("music", True), ("some music", True), ("a song", True), ("something", True), ("me some music", True),
    ("anything", True), ("", True), ("Example Band", False), ("relaxing music", False), ("Fake Hymns", False),
])
def test_a_request_that_names_no_music(words, generic):
    assert fast_media.is_generic_music(words) is generic


def test_the_search_words_never_include_the_question():
    assert gateway_main.extract_media_request("Can you play music?") == ("music", None)
    assert gateway_main.extract_media_request("Play brand in Lake on Office, Jarvis.") == ("brand in Lake", "Office, Jarvis")


@pytest.mark.parametrize("query, kind, value, mine, latest", [
    ("Play my latest audiobook.", "resume", "", True, True),
    ("continue my audiobook", "resume", "", True, True),
    ("Resume my book", "resume", "", True, True),
    ("keep reading", "resume", "", True, True),
    ("continue the audiobook", "resume", "", False, True),
    ("Play the latest audio book by Ginny B. Jones.", "author", "Ginny B Jones", False, True),
    ("play the audiobook by Ann Fake", "author", "Ann Fake", False, False),
    ("Can you play an audiobook by Ann Fake please", "author", "Ann Fake", False, False),
    ("play Ann Fake's newest book", "author", "Ann Fake", False, True),
    ("play the next book in the Fake Saga series", "series", "Fake Saga", False, False),
    ("play The Silent Fakes audiobook", "title", "The Silent Fakes", False, False),
    ("play the audiobook The Silent Fakes", "title", "The Silent Fakes", False, False),
    ("Play Murder on the Fake Express audiobook", "title", "Murder on the Fake Express", False, False),
])
def test_audiobook_requests_are_read(query, kind, value, mine, latest):
    ask, device = fast_media.parse_audiobook_request(query)
    assert (ask.kind, ask.value, ask.mine, ask.latest, device) == (kind, value, mine, latest, None)


def test_a_device_is_split_off_only_when_it_is_one():
    known = {"den speaker"}
    ask, device = fast_media.parse_audiobook_request("play the audiobook by Ann Fake on the den speaker",
                                                     is_device=lambda d: d.lower() in known)
    assert (ask.kind, ask.value, device) == ("author", "Ann Fake", "den speaker")
    ask, device = fast_media.parse_audiobook_request("play the audiobook Murder on the Fake Express",
                                                     is_device=lambda d: d.lower() in known)
    assert (ask.kind, ask.value, device) == ("title", "Murder on the Fake Express", None)


@pytest.mark.parametrize("query", ["Can you play music?", "play Example Band", "play the jungle book",
                                   "pause", "what's on the den tv"])
def test_other_requests_are_not_audiobooks(query):
    assert fast_media.parse_audiobook_request(query) is None


def test_each_audiobook_request_goes_to_its_action():
    base = {"entity_id": SAT_SPEAKER}
    P = fast_media.audiobook_payload
    assert P(fast_media.AudiobookAsk("resume", mine=True), base) == {**base, "action": "resume"}
    assert P(fast_media.AudiobookAsk("author", "Ann Fake", latest=True), base) == \
        {**base, "action": "play_by", "author": "Ann Fake", "latest": True}
    assert P(fast_media.AudiobookAsk("series", "Fake Saga"), base) == \
        {**base, "action": "play_by", "series": "Fake Saga", "latest": False}
    assert P(fast_media.AudiobookAsk("title", "The Silent Fakes"), base) == \
        {**base, "action": "play", "query": "The Silent Fakes"}


def test_the_satellite_speaker_is_used_only_when_it_can_be():
    assert fast_media.satellite_player(VOICE, PLAYERS) == (SAT_SPEAKER, "idle")
    assert fast_media.satellite_player(VOICE, [p for p in PLAYERS if p["entity_id"] != SAT_SPEAKER]) == (None, None)
    gone = [{**p, "state": "unavailable"} if p["entity_id"] == SAT_SPEAKER else p for p in PLAYERS]
    assert fast_media.satellite_player(VOICE, gone) == (None, None)
    assert fast_media.satellite_player({"satellite": SAT}, PLAYERS) == (None, None)
    assert fast_media.satellite_player(None, PLAYERS) == (None, None)


@pytest.mark.parametrize("query, no_device", [
    ("turn it off", True), ("turn off the music", True), ("Jarvis, turn the speaker off", False),
    ("turn off the den TV", False), ("turn on the speaker", True),
])
def test_a_power_command_that_names_no_device(query, no_device):
    assert fast_media.power_names_no_device(query) is no_device


# --------------------------------------------------------------------------- the chat handler


class _Ctx:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *a):
        return False


class _Either(_Ctx):
    def __await__(self):
        async def value():
            return self.resp
        return value().__await__()


def _resp(status, body):
    m = MagicMock()
    m.status = status
    m.json = AsyncMock(return_value=body)
    m.text = AsyncMock(return_value=json.dumps(body))
    m.release = AsyncMock()
    m.raise_for_status = MagicMock()
    return m


def _ask(monkeypatch, query, intent, *, voice=None, user="sam", players=PLAYERS, cache=None,
         reply="Done.", playback="this_device"):
    """Send ``query`` as a satellite would; returns (spoken answer, [(execution path, payload)])."""
    executed: list = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, url, json=None, **kw):
            if "/execute/" not in url:
                return _Either(_resp(200, {}))
            executed.append((url.split("/execute", 1)[1], json))
            return _Either(_resp(200, {"status": "SUCCESS", "message": reply}))

        get = post

    def http_post(url, **kw):
        if url.endswith("/hub/conversation/triggers"):
            return _Ctx(_resp(200, {"matched": False}))
        raise AssertionError(f"the model or anything else must not be asked: {url}")

    sess = MagicMock()
    sess.post = MagicMock(side_effect=http_post)
    sess.get = MagicMock(side_effect=lambda url, **kw: _Ctx(_resp(200, {})))

    eng = IntentEngine()
    eng.update_entity_cache(players)
    monkeypatch.setattr(eng, "classify", lambda q: (intent, 0.99))
    monkeypatch.setattr(gateway_main, "engine", eng)
    monkeypatch.setattr(gateway_main, "INTERNAL_SECRET", SECRET)
    monkeypatch.setattr(gateway_main, "get_http_client", lambda: sess)
    monkeypatch.setattr(gateway_main, "shared_http_client", lambda: _Client())
    monkeypatch.setattr(gateway_main, "fetch_ha_entities", AsyncMock(return_value=players))
    settings = {"voice_satellite_playback": playback}

    async def global_setting(key, default=""):
        return settings.get(key, "0.85")  # every other setting here is the fast path's threshold

    monkeypatch.setattr(gateway_main, "fetch_global_setting", global_setting)
    monkeypatch.setattr(gateway_main, "get_last_used_device", lambda u: {"entity_id": cache} if cache else None)
    monkeypatch.setattr(gateway_main, "set_last_used_device", lambda *a, **k: None)
    monkeypatch.setattr(gateway_main, "update_history", AsyncMock())
    monkeypatch.setattr(gateway_main, "_recognised_speaker", AsyncMock(return_value=None))
    monkeypatch.setattr(gateway_main, "_voice_for_reply", AsyncMock(return_value=None))
    monkeypatch.setattr(gateway_main, "_voice_teaching_reply", AsyncMock(return_value=None))
    monkeypatch.setattr(gateway_main, "_fast_drop_in_answer", AsyncMock(return_value=None))
    monkeypatch.setattr(gateway_main, "_fast_echo_bluetooth_answer", AsyncMock(return_value=None))
    from services.gateway import sentence_triggers

    monkeypatch.setattr(sentence_triggers, "ask_hub", AsyncMock(return_value=None))
    monkeypatch.setattr(gateway_main, "resolve_identity", AsyncMock(return_value={"user": user}))
    body = {"query": query, "model": "any-model", "stream": False}
    if voice is not None:
        body["voice_context"] = dict(voice)
    r = TestClient(gateway_main.app).post("/api/chat", json=body, headers={
        "Authorization": "Bearer made-up-key", "X-Internal-Secret": SECRET})
    assert r.status_code == 200, r.text
    return r.json()["message"]["content"], executed


def test_play_music_at_a_satellite_plays_on_its_own_speaker_not_the_last_tv(monkeypatch):
    said, executed = _ask(monkeypatch, "Can you play music?", "play_media", voice=VOICE, cache=TV)
    assert said == "Done."
    [(path, payload)] = executed
    assert path == "/media/play"
    assert payload["entity_id"] == SAT_SPEAKER
    assert payload["query"] is None and payload["media_type"] == "music"  # never a search for the question


def test_a_named_device_still_wins_over_the_satellite(monkeypatch):
    _, executed = _ask(monkeypatch, "Play Example Band on the kitchen speaker", "play_media", voice=VOICE, cache=TV)
    [(_, payload)] = executed
    assert payload["entity_id"] == KITCHEN and payload["query"] == "Example Band"


def test_without_a_satellite_speaker_the_last_player_is_used_as_before(monkeypatch):
    voice = {k: v for k, v in VOICE.items() if k != "media_player"}
    _, executed = _ask(monkeypatch, "play Example Band", "play_media", voice=voice, cache=TV)
    [(_, payload)] = executed
    assert payload["entity_id"] == TV


def test_a_media_player_claimed_by_an_outside_caller_is_ignored(monkeypatch):
    """voice_context is trusted only with the internal secret: without it the last player is used."""
    monkeypatch.setattr(gateway_main, "INTERNAL_SECRET", SECRET)
    body = {"voice_context": dict(VOICE)}
    assert gateway_main._voice_context_from(SimpleNamespace(headers={"X-Internal-Secret": "wrong"}), body) is None
    ctx = gateway_main._voice_context_from(SimpleNamespace(headers={"X-Internal-Secret": SECRET}),
                                           {"voice_context": dict(VOICE)})
    assert ctx["media_player"] == SAT_SPEAKER
    for bad in ("light.den", "media_player.Den Speaker", "media_player.", 3):
        ctx = gateway_main._voice_context_from(SimpleNamespace(headers={"X-Internal-Secret": SECRET}),
                                               {"voice_context": {**VOICE, "media_player": bad}})
        assert "media_player" not in ctx, bad


def test_pause_at_a_satellite_pauses_its_speaker_when_it_plays(monkeypatch):
    players = [_player(SAT_SPEAKER, "Den Satellite Speaker", "playing"), _player(TV, "Den TV Chrome", "playing")]
    _, executed = _ask(monkeypatch, "pause", "pause_media", voice=VOICE, players=players, cache=TV)
    [(path, payload)] = executed
    assert path == "/media/transport" and payload["entity_id"] == SAT_SPEAKER


def test_pause_at_a_satellite_reaches_what_plays_when_its_speaker_is_quiet(monkeypatch):
    players = [_player(SAT_SPEAKER, "Den Satellite Speaker", "idle"), _player(TV, "Den TV Chrome", "playing")]
    _, executed = _ask(monkeypatch, "pause", "pause_media", voice=VOICE, players=players, cache=TV)
    [(_, payload)] = executed
    assert payload["entity_id"] == TV


def test_turn_off_the_music_at_a_satellite_is_its_own_speaker(monkeypatch):
    players = [_player(SAT_SPEAKER, "Den Satellite Speaker", "playing"), _player(TV, "Den TV Chrome", "on")]
    _, executed = _ask(monkeypatch, "turn off the music", "turn_off", voice=VOICE, players=players, cache=TV)
    [(_, payload)] = executed
    assert payload["entity_id"] == SAT_SPEAKER


def test_an_audiobook_by_an_author_goes_to_audiobookshelf(monkeypatch):
    _, executed = _ask(monkeypatch, "Play the latest audio book by Ginny B. Jones.", "play_media", voice=VOICE,
                       user="household")
    [(path, payload)] = executed
    assert path == "/audiobookshelf"
    assert payload["action"] == "play_by" and payload["author"] == "Ginny B Jones" and payload["latest"] is True
    assert payload["entity_id"] == SAT_SPEAKER


def test_my_audiobook_from_someone_recognised_resumes(monkeypatch):
    _, executed = _ask(monkeypatch, "Play my latest audiobook.", "play_media", voice=VOICE, user="sam")
    [(path, payload)] = executed
    assert path == "/audiobookshelf" and payload["action"] == "resume" and payload["entity_id"] == SAT_SPEAKER


def test_my_audiobook_from_an_unrecognised_voice_says_so(monkeypatch):
    said, executed = _ask(monkeypatch, "Play my latest audiobook.", "play_media", voice=VOICE, user="household")
    assert executed == []
    assert "didn't recognise your voice" in said and "learn my voice" in said


def test_playback_setting_last_used_keeps_the_household_player(monkeypatch):
    _, executed = _ask(monkeypatch, "play Example Band", "play_media", voice=VOICE, cache=TV, playback="last_used")
    [(_, payload)] = executed
    assert payload["entity_id"] == TV


def test_playback_setting_this_device_is_the_default_and_a_bad_value_falls_back_to_it(monkeypatch):
    for value in ("this_device", "", "somewhere_else"):
        _, executed = _ask(monkeypatch, "play Example Band", "play_media", voice=VOICE, cache=TV, playback=value)
        [(_, payload)] = executed
        assert payload["entity_id"] == SAT_SPEAKER, value


def test_an_audiobook_asked_at_a_satellite_plays_on_its_speaker(monkeypatch):
    _, executed = _ask(monkeypatch, "play the latest audiobook by Example Author", "play_media", voice=VOICE, cache=TV)
    [(path, payload)] = executed
    assert path.endswith("audiobookshelf") and payload.get("entity_id") == SAT_SPEAKER
