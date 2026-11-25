""""Play X" searches every media source that is set up, ranks the matches and plays one.

The integrations here are fakes: a hub with (or without) a Music Assistant entry, an
Audiobookshelf login, a book-library setting. No household name, server or title is real.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.common.media_titles import clean_title, normalize, title_matches, title_score
from services.execution import media_finder as mf
from services.execution.media_finder import Candidate as C
from services.execution.schemas import ExecutionResult, MediaPlayRequest, UserContext


@pytest.fixture
def real_library_lists():
    """Asks for the library lists as the fake hub serves them (see ``_no_library_lists``)."""


@pytest.fixture(autouse=True)
def _no_library_lists(request):
    """The library's own lists (``search_ma_library``) are faked per test; by default they list nothing."""
    if "real_library_lists" in request.fixturenames:
        yield
        return
    with patch.object(mf, "search_ma_library", new=AsyncMock(return_value=[])):
        yield


def ctx(**kw) -> UserContext:
    base = {"user": "tester", "ha_url": "http://hub.test:8123", "ha_token": "hub-token"}
    return UserContext(**{**base, **kw})


# --------------------------------------------------------------------------- #
# Words and titles                                                             #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("said", "expected"), [
    ("Moon Garden", ("Moon Garden", None, None)),
    ("the audiobook Moon Garden", ("Moon Garden", "audiobook", None)),
    ("Moon Garden audiobook", ("Moon Garden", "audiobook", None)),
    ("Harbor Lights the song", ("Harbor Lights", "music", "track")),
    ("the album Harbor Lights", ("Harbor Lights", "music", "album")),
    ("the Kitchen Table podcast", ("the Kitchen Table", "podcast", None)),
    ("Pancake Robots on YouTube", ("Pancake Robots", "video", None)),
    ("the movie Pancake Robots", ("Pancake Robots", "video", None)),
    ("a video of Pancake Robots", ("Pancake Robots", "video", None)),
])
def test_split_kind_reads_the_kind_named_and_keeps_the_title(said, expected):
    assert mf.split_kind(said) == expected


def test_a_hint_from_the_caller_applies_only_when_the_words_name_no_kind():
    assert mf.split_kind("Pancake Robots", "video") == ("Pancake Robots", "video", None)
    assert mf.split_kind("Pancake Robots the song", "video")[1] == "music"


def test_split_by_separates_an_artist_or_author():
    assert mf.split_by("Harbor Lights by The Example Band") == ("Harbor Lights", "The Example Band")
    assert mf.split_by("Harbor Lights") == ("Harbor Lights", "")


def test_normalize_ignores_case_punctuation_tags_articles_and_spoken_numbers():
    assert normalize("The Moon Garden (Unabridged)") == "moon garden"
    assert normalize("Moon Garden Three") == "moon garden 3"
    assert normalize("Founders' Key") == normalize("founders key")


def test_title_score_exact_subtitle_contained_and_numbers():
    assert title_score("Moon Garden", "Moon Garden (Unabridged)") == 1.0
    assert title_score("Walking the Long Road", "Walking the Long Road: Notes From a Journey") == 0.98
    assert 0.7 < title_score("Moon Garden", "Moon Garden 2 (Unabridged)") < 0.9
    # A number said is a number wanted: book 3 is not book 1.
    assert title_score("Moon Garden 3", "Moon Garden (Unabridged)") < 0.7
    assert title_score("moon garden three", "Moon Garden 3 (Unabridged)") == 1.0
    assert title_score("first chronicle", "1 Chronicle") == 1.0
    assert title_matches("moon gardin", "Moon Garden")
    assert not title_matches("Harbor", "Moon Garden")


def test_clean_title_drops_edition_tags():
    assert clean_title("Moon Garden (Unabridged)") == "Moon Garden"
    assert clean_title("Old Tales (AmazonClassics Edition) (Unabridged)") == "Old Tales"


# --------------------------------------------------------------------------- #
# Ranking and the decision                                                     #
# --------------------------------------------------------------------------- #

def _series_shelf() -> list[C]:
    return [
        C("audiobookshelf", "audiobook", "Moon Garden (Unabridged)", "abs-1", by="A. Writer"),
        C("audiobookshelf", "audiobook", "Moon Garden 2 (Unabridged)", "abs-2", by="A. Writer"),
        C("audiobookshelf", "audiobook", "Moon Garden 3 (Unabridged)", "abs-3", by="A. Writer"),
        C("audiobookshelf", "series", "Moon Garden", "Moon Garden"),
        C("music_assistant", "audiobook", "Moon Garden (Unabridged)", "library://audiobook/1"),
        C("music_assistant", "radio", "Moon Radio", "radio://1"),
    ]


def test_the_exact_title_plays_and_the_audiobookshelf_copy_wins_over_music_assistants():
    ranked = mf.score_candidates(mf.dedupe(_series_shelf()), "Moon Garden")
    choice = mf.decide(ranked)
    assert choice.action == "play"
    assert (choice.best.source, choice.best.ident) == ("audiobookshelf", "abs-1")
    assert not any(c.source == "music_assistant" and c.kind == "audiobook" for c in ranked)


def test_a_number_picks_that_book_of_the_series():
    choice = mf.decide(mf.score_candidates(mf.dedupe(_series_shelf()), "Moon Garden 3"))
    assert choice.best.ident == "abs-3"


def test_a_series_name_alone_plays_the_series():
    shelf = [C("audiobookshelf", "audiobook", "Sunny Days Collection: Books 1-8 (Unabridged)", "abs-9"),
             C("audiobookshelf", "series", "Sunny Days", "Sunny Days")]
    choice = mf.decide(mf.score_candidates(shelf, "Sunny Days"))
    assert (choice.best.kind, choice.best.ident) == ("series", "Sunny Days")


def test_a_song_and_an_audiobook_with_the_same_name_are_asked_about():
    found = [C("music_assistant", "track", "Harbor", "library://track/7", by="The Example Band"),
             C("audiobookshelf", "audiobook", "Harbor (Unabridged)", "abs-5", by="A. Writer")]
    choice = mf.decide(mf.score_candidates(found, "Harbor"))
    assert choice.action == "ask"
    assert mf.question("Harbor", choice.options) == "Harbor: the song by The Example Band, or the audiobook?"


def test_the_kind_named_settles_the_question():
    found = [C("music_assistant", "track", "Harbor", "library://track/7", by="The Example Band"),
             C("audiobookshelf", "audiobook", "Harbor (Unabridged)", "abs-5")]
    choice = mf.decide(mf.score_candidates(found, "Harbor"), "audiobook")
    assert (choice.action, choice.best.ident) == ("play", "abs-5")


def test_an_artist_named_with_by_picks_their_song():
    found = [C("music_assistant", "track", "Harbor", "library://track/7", by="The Example Band"),
             C("audiobookshelf", "audiobook", "Harbor (Unabridged)", "abs-5", by="A. Writer")]
    choice = mf.decide(mf.score_candidates(found, "Harbor", "the example band"))
    assert (choice.action, choice.best.kind) == ("play", "track")


def test_a_weak_match_is_not_played():
    found = [C("music_assistant", "radio", "Totally Different Station", "radio://2")]
    assert mf.decide(mf.score_candidates(found, "Moon Garden")).action == "none"


def test_not_found_names_the_sources_searched_and_skipped():
    msg = mf.not_found_message("Moon Garden", ["Music Assistant", "Audiobookshelf", "the book library"],
                               ["YouTube (it did not answer)"])
    assert msg == ("I couldn't find Moon Garden in Music Assistant, Audiobookshelf and the book library. "
                   "I couldn't search YouTube (it did not answer).")


# --------------------------------------------------------------------------- #
# Which sources are searched: from the integrations, never a fixed list       #
# --------------------------------------------------------------------------- #

def _settings(values: dict):
    async def read(key: str) -> str:
        return values.get(key, "")
    return read


def _entries(entries: list[dict]):
    async def get(_url, _token, domain=""):
        return [e for e in entries if not domain or e.get("domain") == domain]
    return get


async def _discover(c: UserContext, entries: list[dict], settings: dict, *, screen=False, wants_video=False):
    with patch("services.execution.ha_client.get_config_entries", new=_entries(entries)), \
            patch("services.execution.ha_health.is_down", return_value=False), \
            patch.object(mf, "_setting", new=_settings(settings)):
        return await mf.discover_sources(c, screen=screen, wants_video=wants_video)


@pytest.mark.asyncio
async def test_sources_follow_the_integrations_that_are_set_up():
    c = ctx(audiobookshelf_url="https://abs.test", audiobookshelf_api_key="k", nextcloud_url="https://nc.test")
    sources = await _discover(
        c, [{"domain": "music_assistant", "state": "loaded", "entry_id": "ma1", "title": "Music Assistant"}],
        {"calibre_library_path": "/Books"})
    assert [(s.key, s.ready) for s in sources] == [
        ("music_assistant", True), ("audiobookshelf", True), ("calibre", True)]
    assert sources[0].entry_id == "ma1" and sources[0].route == "hub"


@pytest.mark.asyncio
async def test_nothing_set_up_means_nothing_searched():
    assert await _discover(ctx(), [], {}) == []


@pytest.mark.asyncio
async def test_a_disabled_or_unloaded_entry_is_skipped_with_the_reason():
    c = ctx()
    sources = await _discover(c, [{"domain": "music_assistant", "state": "setup_retry", "entry_id": "m",
                                   "title": "Music Assistant"}], {})
    assert [(s.key, s.ready, s.why) for s in sources] == [("music_assistant", False, "it is setup retry")]
    sources = await _discover(c, [{"domain": "music_assistant", "state": "not_loaded", "disabled_by": "user",
                                   "entry_id": "m", "title": "Music Assistant"}], {})
    assert sources[0].why == "it is turned off"


@pytest.mark.asyncio
async def test_a_persons_own_music_assistant_is_used_without_a_hub_entry():
    sources = await _discover(ctx(mass_url="http://ma.test:8095", mass_token="t"), [], {})
    assert [(s.key, s.route) for s in sources] == [("music_assistant", "direct")]


@pytest.mark.asyncio
async def test_youtube_only_for_a_screen_or_a_video_request_and_never_when_turned_off():
    assert await _discover(ctx(), [], {}, screen=False) == []
    assert [s.key for s in await _discover(ctx(), [], {}, screen=True)] == ["web_video"]
    assert [s.key for s in await _discover(ctx(), [], {}, wants_video=True)] == ["web_video"]
    assert await _discover(ctx(), [], {mf.VIDEO_SEARCH_SETTING: "off"}, screen=True) == []


@pytest.mark.asyncio
async def test_a_source_that_fails_is_skipped_and_said():
    ready = [mf.Source("music_assistant", "Music Assistant", True, route="hub"),
             mf.Source("audiobookshelf", "Audiobookshelf", True)]

    async def search(_ctx, source, _query):
        if source.key == "audiobookshelf":
            raise RuntimeError("401")
        return [C("music_assistant", "track", "Harbor", "u")]

    with patch.object(mf, "_search", new=search):
        cands, searched, skipped = await mf.search_sources(ctx(), ready, "Harbor")
    assert [c.title for c in cands] == ["Harbor"]
    assert searched == ["Music Assistant"]
    assert skipped == ["Audiobookshelf (it did not answer)"]


# --------------------------------------------------------------------------- #
# The whole request                                                            #
# --------------------------------------------------------------------------- #

def _req(query: str, **kw) -> MediaPlayRequest:
    return MediaPlayRequest(user_context=ctx(), query=query, search_everywhere=True, **kw)


def _ready(*keys: str) -> list[mf.Source]:
    labels = {"music_assistant": "Music Assistant", "audiobookshelf": "Audiobookshelf",
              "calibre": "the book library", "web_video": "YouTube"}
    return [mf.Source(k, labels[k], True, route="hub" if k == "music_assistant" else "") for k in keys]


def _found(by_source: dict[str, list[C]]):
    async def search(_ctx, source, _query):
        return list(by_source.get(source.key, []))
    return search


async def _run(req, *, screen=False, sources=None, found=None, player="Den Speaker", facts=None):
    from services.execution import video_safety
    from services.execution.handlers import video as video_handler

    video_safety._checked.clear()
    facts = facts or {}

    async def inspect(url):
        return {"title": "", "channel": "", "age_limit": 0, **facts.get(url, {})}

    played = AsyncMock(side_effect=lambda cand, *_a, **_k: ExecutionResult.ok(f"played {cand.ident}", service="t"))
    with patch.object(video_safety, "_setting", new=_settings({})), \
            patch.object(video_handler, "inspect_video", new=inspect), \
            patch.object(mf, "target_facts", new=AsyncMock(return_value=(screen, player))), \
            patch.object(mf, "discover_sources", new=AsyncMock(return_value=sources or [])), \
            patch.object(mf, "_search", new=_found(found or {})), \
            patch.object(mf, "corrected_query", new=AsyncMock(return_value=None)), \
            patch.object(mf, "screens_listed", new=AsyncMock(return_value=[("media_player.den_tv", "Den TV")])), \
            patch.object(mf, "play_candidate", new=played):
        result = await mf.find_and_play(req, "media_player.den", req.user_context)
    return result, played


@pytest.mark.asyncio
async def test_an_audiobook_is_found_when_music_has_nothing():
    result, played = await _run(
        _req("Moon Garden"), sources=_ready("music_assistant", "audiobookshelf"),
        found={"audiobookshelf": _series_shelf()[:4], "music_assistant": [C("music_assistant", "radio", "Moon Radio", "r")]})
    assert result.message == "played abs-1"
    assert played.await_args.args[0].kind == "audiobook"


@pytest.mark.asyncio
async def test_a_tie_asks_and_carries_the_options_for_the_answer():
    result, played = await _run(
        _req("Harbor"), sources=_ready("music_assistant", "audiobookshelf"),
        found={"music_assistant": [C("music_assistant", "track", "Harbor", "library://track/7", by="The Example Band")],
               "audiobookshelf": [C("audiobookshelf", "audiobook", "Harbor (Unabridged)", "abs-5")]})
    assert played.await_count == 0
    assert result.message.endswith("or the audiobook?")
    choice = result.detail["choice"]
    assert choice["entity_id"] == "media_player.den"
    assert [o["pick"]["ident"] for o in choice["options"]] == ["library://track/7", "abs-5"]


@pytest.mark.asyncio
async def test_nothing_anywhere_says_what_was_searched():
    result, played = await _run(_req("Moon Garden"), sources=_ready("music_assistant", "audiobookshelf", "calibre"))
    assert result.status == "FAILURE" and played.await_count == 0
    assert result.message == "I couldn't find Moon Garden in Music Assistant, Audiobookshelf and the book library."


@pytest.mark.asyncio
async def test_a_book_only_in_the_library_is_offered_to_be_read():
    result, played = await _run(
        _req("Moon Garden"), sources=_ready("music_assistant", "calibre"),
        found={"calibre": [C("calibre", "book", "Moon Garden", "42", by="A. Writer")]})
    assert played.await_count == 0
    assert result.message.endswith("Do you want me to read it to you?")
    assert result.detail["choice"]["options"][0]["read"]["book_id"] == "42"


@pytest.mark.asyncio
async def test_a_video_on_a_speaker_asks_for_a_screen_instead():
    result, played = await _run(_req("the movie Pancake Robots"), screen=False, sources=_ready("music_assistant"))
    assert played.await_count == 0 and result.detail["reason"] == "which_screen"
    assert result.message == "Should I put it on the Den TV?"
    [option] = result.detail["choice"]["options"]
    assert option["entity_id"] == "media_player.den_tv"


@pytest.mark.asyncio
async def test_a_video_kind_with_no_picture_word_still_names_a_screen():
    """media_type "video" from the caller, but neither a picture word nor YouTube: the old answer."""
    result, played = await _run(_req("Pancake Robots", media_type="video"), screen=False,
                                sources=_ready("music_assistant"))
    assert result.status == "FAILURE" and played.await_count == 0
    assert result.message == "Den Speaker can't show video. Say which one, for example 'on the Den TV'."


@pytest.mark.asyncio
async def test_a_video_on_a_screen_plays_from_youtube():
    _result, played = await _run(
        _req("the movie Pancake Robots"), screen=True, sources=_ready("music_assistant", "web_video"),
        found={"web_video": [C("web_video", "video", "Pancake Robots Full Movie", "https://youtube.test/v1")],
               "music_assistant": [C("music_assistant", "track", "Pancake Robots", "library://track/3")]})
    assert played.await_args.args[0].source == "web_video"


@pytest.mark.asyncio
async def test_a_screen_plays_the_library_match_and_only_searches_the_web_when_there_is_none():
    _result, played = await _run(
        _req("Harbor"), screen=True, sources=_ready("music_assistant", "web_video"),
        found={"music_assistant": [C("music_assistant", "track", "Harbor", "library://track/7")],
               "web_video": [C("web_video", "video", "Harbor", "https://youtube.test/v2")]})
    assert played.await_args.args[0].source == "music_assistant"
    _result, played = await _run(
        _req("Pancake Robots"), screen=True, sources=_ready("music_assistant", "web_video"),
        found={"web_video": [C("web_video", "video", "Pancake Robots", "https://youtube.test/v1")]})
    assert played.await_args.args[0].source == "web_video"


@pytest.mark.asyncio
async def test_a_chosen_option_plays_as_found_without_searching_again():
    pick = C("audiobookshelf", "audiobook", "Harbor (Unabridged)", "abs-5").as_pick()
    req = _req("Harbor", pick=pick)
    search = AsyncMock()
    played = AsyncMock(return_value=ExecutionResult.ok("ok", service="t"))
    with patch.object(mf, "target_facts", new=AsyncMock(return_value=(False, "Den Speaker"))), \
            patch.object(mf, "discover_sources", new=AsyncMock(return_value=_ready("audiobookshelf"))), \
            patch.object(mf, "_search", new=search), patch.object(mf, "play_candidate", new=played):
        await mf.find_and_play(req, "media_player.den", req.user_context)
    assert search.await_count == 0
    assert played.await_args.args[0].ident == "abs-5"


@pytest.mark.asyncio
async def test_a_chosen_option_from_a_source_that_went_away_is_refused():
    pick = C("audiobookshelf", "audiobook", "Harbor", "abs-5").as_pick()
    req = _req("Harbor", pick=pick)
    with patch.object(mf, "target_facts", new=AsyncMock(return_value=(False, "Den Speaker"))), \
            patch.object(mf, "discover_sources", new=AsyncMock(return_value=_ready("music_assistant"))):
        result = await mf.find_and_play(req, "media_player.den", req.user_context)
    assert result.status == "FAILURE"


# --------------------------------------------------------------------------- #
# Playing goes to the handler that already plays that kind                     #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_play_candidate_routes_each_source_to_its_handler():
    req = _req("x")
    c = req.user_context
    ok = ExecutionResult.ok("ok", service="t")
    with patch("services.execution.handlers.audiobookshelf.handle_audiobookshelf", new=AsyncMock(return_value=ok)) as abs_play:
        out = await mf.play_candidate(C("audiobookshelf", "audiobook", "Moon Garden (Unabridged)", "abs-1"),
                                      req, "media_player.den", c, "Den Speaker")
        assert abs_play.await_args.args[0].action == "play" and abs_play.await_args.args[0].book_id == "abs-1"
        assert out.message == "Playing Moon Garden, the audiobook, on Den Speaker."
        await mf.play_candidate(C("audiobookshelf", "series", "Sunny Days", "Sunny Days"), req, "media_player.den", c)
        assert abs_play.await_args.args[0].action == "play_by" and abs_play.await_args.args[0].series == "Sunny Days"
        await mf.play_candidate(C("audiobookshelf", "podcast", "Kitchen Table", "pod-1"), req, "media_player.den", c)
        assert abs_play.await_args.args[0].action == "play_podcast_episode"
    with patch("services.execution.handlers.media.play_music", new=AsyncMock(return_value=ok)) as music:
        await mf.play_candidate(C("music_assistant", "track", "Harbor", "library://track/7"), req, "media_player.den", c)
        assert music.await_args.args[0].query == "library://track/7"
        assert music.await_args.args[0].search_everywhere is False
    with patch("services.execution.handlers.media.play_video", new=AsyncMock(return_value=ok)) as video:
        out = await mf.play_candidate(C("web_video", "video", "Pancake Robots", "https://youtube.test/v1",
                                        extra={"title_known": True}), req, "media_player.den", c, "Den TV")
        assert video.await_args.args[0].query == "https://youtube.test/v1"
        assert out.message == "Playing the video Pancake Robots on Den TV."


@pytest.mark.asyncio
async def test_handle_media_play_sends_search_everywhere_to_the_finder():
    from services.execution.handlers import media as media_handler

    req = _req("Moon Garden", entity_id="media_player.den")
    with patch.object(media_handler, "resolve_entity", new=AsyncMock(return_value="media_player.den")), \
            patch("services.execution.ha_client.get_state", new=AsyncMock(return_value={"state": "idle"})), \
            patch("services.execution.activity.record", new=AsyncMock()) as record, \
            patch.object(mf, "find_and_play", new=AsyncMock(return_value=ExecutionResult.ok(
                "Moon Garden: the song, or the audiobook?", service="t", detail={"route": "ask"}))) as finder:
        result = await media_handler.handle_media_play(req)
    assert finder.await_count == 1
    assert result.detail["route"] == "ask"
    assert record.await_count == 0  # a question is not a play


@pytest.mark.asyncio
async def test_audiobookshelf_is_matched_by_the_shared_title_rules_not_its_literal_search():
    catalogue = [
        {"id": "b1", "kind": "audiobook", "title": "Sunny Days #25: A Long Subtitle", "author": "Ann Writer",
         "series": ["Sunny Days"]},
        {"id": "b2", "kind": "audiobook", "title": "1 Chronicle", "author": "Ben Reader", "series": ["Study Set"]},
        {"id": "p1", "kind": "podcast", "title": "Dr. Example's Kitchen Table", "author": "Example Radio", "series": []},
    ]
    with patch.object(mf, "abs_catalogue", new=AsyncMock(return_value=catalogue)):
        sunny = await mf.search_audiobookshelf(ctx(), "Sunny Days")
        first = await mf.search_audiobookshelf(ctx(), "first chronicle")
        table = await mf.search_audiobookshelf(ctx(), "Kitchen Table")
        author = await mf.search_audiobookshelf(ctx(), "Ann Writer")
    assert mf.decide(mf.score_candidates(sunny, "Sunny Days")).best.kind == "series"
    assert mf.decide(mf.score_candidates(first, "first chronicle")).best.ident == "b2"
    assert mf.decide(mf.score_candidates(table, "Kitchen Table")).best.ident == "p1"
    assert [(c.kind, c.ident) for c in author] == [("author", "Ann Writer")]


def test_the_same_audiobook_with_a_subtitle_in_music_assistant_is_one_item():
    found = [C("audiobookshelf", "audiobook", "Moon Garden (Unabridged)", "abs-1"),
             C("music_assistant", "audiobook", "Moon Garden (Unabridged) | A Subtitle", "library://audiobook/1")]
    assert [c.source for c in mf.dedupe(found)] == ["audiobookshelf"]
    assert clean_title("Moon Garden (Unabridged) | A Subtitle") == "Moon Garden"


def test_a_name_split_by_speech_recognition_still_matches():
    assert title_score("Kay 9", "KAY9") >= 0.98
    assert title_score("Kay Nine", "KAY9") >= 0.98


def test_a_refused_item_is_named_with_the_reason_in_plain_words():
    cand = C("music_assistant", "playlist", "Road Songs", "library://playlist/2")
    assert mf.failed_message(cand, "Failed to play direct URI: HA returned 400: 999: No playable items found",
                             "Den Speaker") == "I found Road Songs, the playlist, but there's nothing in it I can play."
    assert mf.failed_message(cand, "HA returned 500: device offline", "Den Speaker") == (
        "I found Road Songs, the playlist, but Den Speaker wouldn't play it: device offline")


@pytest.mark.asyncio
async def test_call_service_passes_on_the_hubs_reason(monkeypatch):
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    from services.execution import ha_client

    async def refuse(_request):
        return web.json_response({"message": "999: No playable items found"}, status=400)

    app = web.Application()
    app.router.add_post("/api/services/music_assistant/play_media", refuse)
    async with TestServer(app) as server:
        monkeypatch.setattr(ha_client.ha_health, "skip_reason", lambda _url: None)
        url = str(server.make_url("")).rstrip("/")
        res = await ha_client.call_service(url, "tok", "music_assistant", "play_media", "media_player.den",
                                           {"media_id": "library://playlist/2"})
    assert res["ok"] is False and res["status_code"] == 400
    assert res["error"] == "HA returned 400: 999: No playable items found"


# --------------------------------------------------------------------------- #
# Heard wrong: titles that sound right                                         #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(("said", "title"), [
    ("moon gardin", "Moon Garden (Unabridged)"),
    ("sunny bee days", "Sunny B. Days"),
    ("the grey harbors", "The Gray Harbors"),
    ("kay nine", "KAY9"),
])
def test_a_title_that_sounds_the_same_is_matched(said, title):
    assert title_score(said, title) >= 0.7


@pytest.mark.parametrize(("said", "title"), [
    ("dune", "June"), ("genesis", "Exodus"), ("return", "Reunion"), ("wild at heart", "Wild Things"),
    ("moon garden 3", "Moon Garden 2"),
])
def test_a_title_that_only_looks_or_sounds_a_little_alike_is_not(said, title):
    assert title_score(said, title) < 0.7


@pytest.mark.asyncio
async def test_music_assistants_stations_and_playlists_are_matched_by_sound_too():
    src = mf.Source("music_assistant", "Music Assistant", True, route="hub", entry_id="ma1")
    library = [C("music_assistant", "radio", "ARE7", "library://radio/1"),
               C("music_assistant", "playlist", "Road Songs", "library://playlist/2")]
    with patch.object(mf, "_search_music_assistant", new=AsyncMock(return_value=[])), \
            patch.object(mf, "_ma_library_names", new=AsyncMock(return_value=library)):
        found = await mf.search_music_assistant(ctx(), src, "er seven")  # "Play ARE7" heard as "Player seven"
    choice = mf.decide(mf.score_candidates(found, "er seven"))
    assert choice.action == "play" and choice.best.ident == "library://radio/1"
    assert library[0].score == 0.0  # the cached list is not scored in place


@pytest.mark.asyncio
async def test_find_only_names_the_best_match_and_plays_nothing():
    req = _req("Moon Garden")
    played = AsyncMock()
    with patch.object(mf, "discover_sources", new=AsyncMock(return_value=_ready("audiobookshelf", "calibre"))), \
            patch.object(mf, "_search", new=_found({"audiobookshelf": _series_shelf()[:1],
                                                    "calibre": [C("calibre", "book", "Moon Garden", "1")]})), \
            patch.object(mf, "play_candidate", new=played):
        result = await mf.find_only(req)
    assert played.await_count == 0
    assert result.detail["action"] == "play" and result.detail["score"] == 1.0
    assert result.detail["best"]["ident"] == "abs-1"
    assert result.detail["searched"] == ["Audiobookshelf"]  # not the book library


# --------------------------------------------------------------------------- an artist misheard

def test_an_artist_that_sounds_the_same_picks_their_song():
    # 2026-11-24: "Play Return by The Gray Havens" was heard "by the Great Evens" and nothing played.
    found = [C("music_assistant", "track", "Harbor", "library://track/7", by="The Example Havens"),
             C("audiobookshelf", "audiobook", "Harbor (Unabridged)", "abs-5", by="A. Writer")]
    choice = mf.decide(mf.score_candidates(found, "Harbor", "the Example Evens"))
    assert (choice.action, choice.best.ident) == ("play", "library://track/7")


def test_an_artist_that_only_looks_a_little_alike_does_not_count():
    found = [C("music_assistant", "track", "Harbor", "library://track/7", by="The Example Havens")]
    choice = mf.decide(mf.score_candidates(found, "Harbor", "Totally Other Group"))
    assert choice.action == "none"


def test_a_misheard_artist_is_heard_as_the_librarys_when_one_sounds_like_it():
    from services.execution import media_vocabulary

    media_vocabulary.reset()
    try:
        media_vocabulary.remember("artist", ["The Example Havens", "Some Other Band"])
        assert mf.known_name("the Example Evens") == "The Example Havens"
        assert mf.known_name("Some Other Band") == "Some Other Band"
        assert mf.known_name("Nobody We Know") == "Nobody We Know"
    finally:
        media_vocabulary.reset()


@pytest.mark.asyncio
async def test_title_by_artist_also_searches_the_title_with_the_artist():
    queries: list[str] = []

    async def search(_ctx, source, query):
        queries.append(query)
        if query == "Harbor The Example Havens":
            return [C("music_assistant", "track", "Harbor", "library://track/7", by="The Example Havens")]
        return [C("music_assistant", "track", "Harbor", "library://track/1", by="Somebody Else")]

    from services.execution import media_vocabulary

    media_vocabulary.reset()
    media_vocabulary.remember("artist", ["The Example Havens"])
    try:
        played = AsyncMock(side_effect=lambda cand, *_a, **_k: ExecutionResult.ok(f"played {cand.ident}", service="t"))
        with patch.object(mf, "target_facts", new=AsyncMock(return_value=(False, "Den Speaker"))), \
                patch.object(mf, "discover_sources", new=AsyncMock(return_value=_ready("music_assistant"))), \
                patch.object(mf, "_search", new=search), \
                patch.object(mf, "corrected_query", new=AsyncMock(return_value=None)), \
                patch.object(mf, "play_candidate", new=played):
            result = await mf.find_and_play(_req("Harbor by the Example Evens"), "media_player.den", ctx())
    finally:
        media_vocabulary.reset()
    assert "Harbor The Example Havens" in queries
    assert result.message == "played library://track/7"


# --------------------------------------------------------------------------- short titles and joint artists
# 2026-11-24: "Play Up by the artist, Forest Frank and Connor Price" found nothing. The library has
# the track "UP!" credited to the one artist entry "Forrest Frank & Connor Price"; Music Assistant's
# search misses a title that short, but its library list of tracks named "up" has it. Names are fake.

_TRACKS = [
    {"name": "UP!", "uri": "library://track/41", "artists": [{"name": "Forrest Fenn & Connor Pike"}]},
    {"name": "Up and Away", "uri": "library://track/42", "artists": [{"name": "Somebody Else"}]},
    {"name": "Glow", "uri": "library://track/43", "artists": [{"name": "Example Singer feat. Other Voice"}]},
    {"name": "Don't Stop!", "uri": "library://track/44", "artists": [{"name": "Example Band"}]},
]
_ALBUMS = [{"name": "Rise Up", "uri": "library://album/9", "artists": [{"name": "Another Band"}]}]


def _fake_hub(searched: list[str] | None = None):
    """Patches for a hub whose Music Assistant ``search`` finds only the album "Rise Up" for "up",
    and whose ``get_library`` filters its tracks and albums by the words, as MA's library does."""
    from services.execution import ha_client
    from services.execution.handlers import mass_ha_client

    async def call_service(_url, _token, _domain, _service, **kw):
        name = kw["service_data"]["name"]
        if searched is not None:
            searched.append(name)
        albums = [dict(a, media_type="album") for a in _ALBUMS if name.lower() == "up"]
        return {"ok": True, "service_response": {"albums": albums}}

    async def ma_service(_url, _token, service, data):
        assert service == "get_library"
        items = {"track": _TRACKS, "album": _ALBUMS}.get(data["media_type"], [])
        words = (data.get("search") or "").lower()
        return {"service_response": {"items": [i for i in items if words in i["name"].lower()]}}

    return (patch.object(ha_client, "call_service", new=call_service),
            patch.object(mass_ha_client, "_call_ha_ma_service", new=ma_service))


async def _lookup(query: str) -> ExecutionResult:
    from services.execution import media_vocabulary

    media_vocabulary.reset()
    hub_search, hub_library = _fake_hub()
    src = mf.Source("music_assistant", "Music Assistant", True, route="hub", entry_id="ma1")
    mf._ma_library_cache.clear()
    with hub_search, hub_library, patch.object(mf, "discover_sources", new=AsyncMock(return_value=[src])):
        return await mf.find_only(MediaPlayRequest(user_context=ctx(), query=query, search_everywhere=True))


def test_titles_ignore_punctuation_and_case():
    assert normalize("UP!") == normalize("up") == "up"
    assert normalize("Don't") == normalize("dont")
    assert title_score("dont stop", "Don't Stop!") == 1.0


def test_joint_artist_credits_match_any_order_and_spelling():
    from services.common.media_titles import artists_match, split_artists

    assert split_artists("Forrest Fenn & Connor Pike") == ["Forrest Fenn", "Connor Pike"]
    assert split_artists("Example Singer feat. Other Voice") == ["Example Singer", "Other Voice"]
    assert split_artists("A x B, C (ft. D) with E") == ["A", "B", "C", "D", "E"]
    credit = "Forrest Fenn & Connor Pike"
    for asked in ("Forest Fenn and Connor Pike", "Connor Pike and Forrest Fenn", "Forest Fenn", "connor pike"):
        assert artists_match(asked, credit), asked
    assert not artists_match("Totally Other Group", credit)
    assert not artists_match("Forrest Fenn and Totally Other", credit)


@pytest.mark.parametrize(("said", "expected"), [
    ("up by the artist, Forest Fenn and Connor Pike", ("up", "Forest Fenn and Connor Pike")),
    ("Up by the band Example Band", ("Up", "Example Band")),
    ("Up by artist Example Band", ("Up", "Example Band")),
])
def test_by_the_artist_fillers_are_not_part_of_the_name(said, expected):
    assert mf.split_by(said) == expected


def test_a_short_title_is_never_found_inside_a_longer_one():
    found = [C("music_assistant", "album", "Rise Up", "library://album/9")]
    assert mf.decide(mf.score_candidates(found, "Up")).action == "none"
    assert mf.decide(mf.score_candidates([C("music_assistant", "track", "UP!", "t")], "Up")).action == "play"


@pytest.mark.asyncio
@pytest.mark.parametrize("said", [
    "up by the artist, Forest Fenn and Connor Pike",
    "song called Up by Forrest Fenn and Connor Pike",
    "Up by Forrest Fenn",
    "Up Forrest Fenn",
    "Forrest Fenn Up",
    "Up",
])
async def test_a_short_title_is_found_in_the_artists_library_tracks(said, real_library_lists):
    result = await _lookup(said)
    assert result.detail["action"] == "play", result.message
    assert result.detail["best"]["ident"] == "library://track/41"
    assert result.detail["best"]["by"] == "Forrest Fenn & Connor Pike"


@pytest.mark.asyncio
async def test_a_featured_artist_and_an_apostrophe_are_found(real_library_lists):
    glow = await _lookup("Glow by Other Voice")
    assert glow.detail["best"]["ident"] == "library://track/43"
    dont = await _lookup("Dont Stop by Example Band")
    assert dont.detail["best"]["ident"] == "library://track/44"


@pytest.mark.asyncio
async def test_a_short_title_by_the_wrong_artist_is_not_played(real_library_lists):
    result = await _lookup("Up by Totally Other Group")
    assert result.detail["action"] == "none"
