"""Web video on a household screen: safe search, screening, and never autoplaying a weak match.

Every search result, title, channel and URL here is made up. The first case is the one that
happened: a misheard "Keepy Uppy" ("keeping up beyond YouTube") must not play a celebrity
gossip clip on the family TV.
"""
from __future__ import annotations

import http.cookiejar
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

from services.execution import media_finder as mf
from services.execution import video_safety as vs
from services.execution.handlers import video as video_handler
from services.execution.media_finder import Candidate as C
from services.execution.schemas import ExecutionResult, MediaPlayRequest, UserContext

GOSSIP = C("web_video", "video", 'KUWTK | Celebrity Has "Had It" With The Haters | Gossip Channel',
           "https://youtube.test/watch?v=gossip1", by="Gossip Channel")
EPISODE = C("web_video", "video", "Puppy Show | Keepy Uppy | Full Episode", "https://youtube.test/watch?v=ep1",
            by="Puppy Show Official")


def ctx(**kw) -> UserContext:
    base = {"user": "tester", "ha_url": "http://hub.test:8123", "ha_token": "hub-token"}
    return UserContext(**{**base, **kw})


def _settings(values: dict):
    async def read(key: str) -> str:
        return values.get(key, "")
    return read


def _req(query: str, **kw) -> MediaPlayRequest:
    return MediaPlayRequest(user_context=kw.pop("user_context", ctx()), query=query, search_everywhere=True, **kw)


def _fresh(*cands: C) -> list[C]:
    return [C(c.source, c.kind, c.title, c.ident, by=c.by, extra=dict(c.extra)) for c in cands]


async def _run(req, found, *, facts=None, settings=None):
    """find_and_play on a TV whose only source is YouTube, with fake results and fake site facts."""
    vs._checked.clear()
    facts = facts or {}
    inspected: list[str] = []

    async def inspect(url):
        inspected.append(url)
        f = facts.get(url, {})
        if isinstance(f, Exception):
            raise f
        return {"title": "", "channel": "", "age_limit": 0, **f}

    async def search(_ctx, source, _query):
        return _fresh(*found) if source.key == "web_video" else []

    played = AsyncMock(side_effect=lambda cand, *_a, **_k: ExecutionResult.ok(f"played {cand.ident}", service="t"))
    with patch.object(vs, "_setting", new=_settings(settings or {})), \
            patch.object(video_handler, "inspect_video", new=inspect), \
            patch.object(mf, "target_facts", new=AsyncMock(return_value=(True, "Office TV"))), \
            patch.object(mf, "discover_sources", new=AsyncMock(return_value=[mf.Source("web_video", "YouTube", True)])), \
            patch.object(mf, "_search", new=search), \
            patch.object(mf, "corrected_query", new=AsyncMock(return_value=None)), \
            patch.object(mf, "play_candidate", new=played):
        result = await mf.find_and_play(req, "media_player.office_tv", req.user_context)
    return result, played, inspected


# --------------------------------------------------------------------------- #
# The misheard request, and a strong match                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_the_misheard_request_does_not_autoplay_a_gossip_clip(caplog):
    caplog.set_level("INFO", logger="execution.video_safety")
    result, played, _ = await _run(_req("keeping up beyond YouTube", media_type="video"), [GOSSIP])
    assert played.await_count == 0
    assert result.detail["route"] == "ask"
    assert result.message.startswith("I found '") and result.message.endswith("on YouTube. Play it?")
    assert vs.match_score("keeping up beyond", GOSSIP.title, GOSSIP.by) < vs.STRONG_MATCH
    [line] = [r.getMessage() for r in caplog.records if "[Reply] video_search" in r.getMessage()]
    assert "decision=ask" in line and "safe_search=strict" in line and "tester" not in line


@pytest.mark.asyncio
async def test_a_strong_match_autoplays_even_below_a_weaker_first_result():
    result, played, _ = await _run(_req("Keepy Uppy", media_type="video"), [GOSSIP, EPISODE])
    assert result.message == "played https://youtube.test/watch?v=ep1"
    assert played.await_args.args[0].score >= vs.STRONG_MATCH


def test_youtube_title_parts_and_filler_are_matched_by_the_library_rules():
    assert vs.match_score("Keepy Uppy", EPISODE.title) == 1.0
    assert vs.match_score("Pancake Robots", "Pancake Robots Full Movie HD") == 1.0
    assert vs.match_score("keepy upy", "Puppy Show: Keepy Uppy") >= vs.STRONG_MATCH  # misspelt, sounds the same
    assert vs.match_score("Moon Garden", "Totally Different Video") == 0.0


# --------------------------------------------------------------------------- #
# Screening                                                                    #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_an_age_restricted_result_is_skipped_for_the_next_suitable_one():
    adult = C("web_video", "video", "Keepy Uppy", "https://youtube.test/watch?v=adult1")
    result, played, _ = await _run(_req("Keepy Uppy", media_type="video"), [adult, EPISODE],
                                   facts={adult.ident: {"age_limit": 18}})
    assert result.message == "played https://youtube.test/watch?v=ep1"


@pytest.mark.asyncio
async def test_only_age_restricted_results_play_nothing_and_say_so():
    teen = C("web_video", "video", "Keepy Uppy", "https://youtube.test/watch?v=teen1")
    gated = C("web_video", "video", "Keepy Uppy Live", "https://youtube.test/watch?v=gated1")
    result, played, _ = await _run(
        _req("Keepy Uppy", media_type="video"), [teen, gated],
        facts={teen.ident: {"age_limit": 13}, gated.ident: RuntimeError("Sign in to confirm your age")})
    assert played.await_count == 0 and result.status == "FAILURE"
    assert result.message == "I didn't find a video of Keepy Uppy on YouTube that's suitable to play here."
    assert result.detail["reason"] == "age_restricted"


@pytest.mark.asyncio
async def test_a_rating_marked_in_the_search_result_is_skipped_without_asking_the_site():
    flagged = C("web_video", "video", "Keepy Uppy", "https://youtube.test/watch?v=flag1", extra={"age_limit": 18})
    result, _played, inspected = await _run(_req("Keepy Uppy", media_type="video"), [flagged, EPISODE])
    assert flagged.ident not in inspected
    assert result.message == "played https://youtube.test/watch?v=ep1"


@pytest.mark.asyncio
async def test_a_video_whose_rating_cannot_be_checked_is_not_played():
    result, played, _ = await _run(_req("Keepy Uppy", media_type="video"), [EPISODE],
                                   facts={EPISODE.ident: RuntimeError("network down")})
    assert played.await_count == 0 and result.detail["reason"] == "unchecked"


@pytest.mark.asyncio
async def test_the_blocklist_skips_a_title_or_channel_and_is_empty_by_default():
    result, played, _ = await _run(_req("Keepy Uppy", media_type="video"), [EPISODE],
                                   settings={vs.BLOCKLIST_SETTING: "gossip,\nPuppy Show Official"})
    assert played.await_count == 0 and result.detail["reason"] == "blocked"
    assert vs.blocked_by(EPISODE.title, EPISODE.by, []) is None
    assert vs.blocked_by("A gossiping parrot", "", ["gossip"]) is None  # whole words only


def test_age_limits_for_shared_devices_children_and_adults():
    adult, child = ctx(), ctx(is_minor=True)
    assert vs.age_refused(13, adult, shared=True)
    assert not vs.age_refused(12, adult, shared=True)
    assert vs.age_refused(13, child, shared=False)
    assert not vs.age_refused(13, adult, shared=False)
    assert vs.age_refused(18, adult, shared=False)
    assert vs.age_refused("not a number", adult, shared=False)


# --------------------------------------------------------------------------- #
# Safe search                                                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "who", "expected"), [
    ("", ctx(), "strict"),                          # unset or Identity unreachable
    ("strict", ctx(), "strict"),
    ("moderate", ctx(), "moderate"),                # an admin relaxed it, for a recognised adult
    ("off", ctx(), "off"),
    ("moderate", ctx(is_minor=True), "strict"),     # never relaxed for a child
    ("off", ctx(user="household"), "strict"),       # nor for a speaker nobody recognised
    ("off", None, "strict"),
    ("lenient", ctx(), "strict"),                   # an unknown value is strict, and logged
])
async def test_safe_search_level(value, who, expected):
    with patch.object(vs, "_setting", new=_settings({vs.SAFE_SEARCH_SETTING: value})):
        assert await vs.safe_search_level(who) == expected


def _fake_yt_dlp(entries=None, fail=False):
    jars: list[http.cookiejar.CookieJar] = []

    class YoutubeDL:
        def __init__(self, _opts):
            self.cookiejar = http.cookiejar.CookieJar()
            jars.append(self.cookiejar)

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def extract_info(self, query, download=False, **_kw):
            if fail:
                raise RuntimeError("search unavailable")
            return {"entries": entries or []}

    return types.SimpleNamespace(YoutubeDL=YoutubeDL), jars


@pytest.mark.asyncio
async def test_youtube_search_asks_for_restricted_mode_and_returns_titles_and_channels(monkeypatch):
    fake, jars = _fake_yt_dlp([{"url": "https://youtube.test/watch?v=ep1", "title": "Keepy Uppy",
                                "channel": "Puppy Show Official"}])
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    found = await video_handler.search_youtube_entries("keepy uppy", "strict")
    assert found == [{"url": "https://youtube.test/watch?v=ep1", "title": "Keepy Uppy",
                      "channel": "Puppy Show Official", "age_limit": None}]
    assert [(c.name, c.value, c.domain) for c in jars[0]] == [("PREF", "f2=8000000", ".youtube.com")]
    await video_handler.search_youtube_entries("keepy uppy", "off")
    assert list(jars[-1]) == []


class _FakeResponse:
    def raise_for_status(self):
        return None

    async def text(self):
        return '<a href="https://www.youtube.com/watch?v=ep1">x</a>'

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False


@pytest.mark.asyncio
async def test_searxng_is_asked_for_its_strictest_safe_search(monkeypatch):
    fake, _jars = _fake_yt_dlp(fail=True)
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    asked: list[str] = []

    class Session:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        def get(self, url, **_kw):
            asked.append(url)
            return _FakeResponse()

    monkeypatch.setattr(video_handler, "_get_searxng_url", AsyncMock(return_value="http://searx.test"))
    monkeypatch.setattr(video_handler.aiohttp, "ClientSession", Session)
    assert await video_handler.search_youtube("keepy uppy", "strict") == "https://www.youtube.com/watch?v=ep1"
    assert "safesearch=2" in asked[0]
    await video_handler.search_youtube("keepy uppy", "moderate")
    assert "safesearch=1" in asked[1]


# --------------------------------------------------------------------------- #
# The question and its answer                                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_the_question_carries_one_option_a_yes_plays():
    result, _played, _ = await _run(_req("keeping up beyond YouTube", media_type="video"), [GOSSIP])
    choice = result.detail["choice"]
    assert choice["entity_id"] == "media_player.office_tv"
    [option] = choice["options"]
    assert option["pick"]["ident"] == GOSSIP.ident and "yes" in option["words"]


@pytest.mark.asyncio
async def test_a_chosen_video_is_screened_again_before_it_plays():
    """The "yes" plays the pick through play_video, which checks the video once more."""
    from services.execution.handlers import media as media_handler

    vs._checked.clear()
    req = _req("https://www.youtube.com/watch?v=fake0001")
    with patch.object(vs, "_setting", new=_settings({})), \
            patch.object(video_handler, "inspect_video", new=AsyncMock(return_value={"age_limit": 18})), \
            patch.object(video_handler, "download_video_progressive", new=AsyncMock()) as download:
        result = await media_handler.play_video(req, "media_player.office_tv", req.user_context)
    assert result.status == "FAILURE" and result.detail["reason"] == "age_restricted"
    assert download.await_count == 0


@pytest.mark.asyncio
async def test_play_video_with_words_never_plays_the_first_result_blindly():
    from services.execution.handlers import media as media_handler

    vs._checked.clear()
    req = MediaPlayRequest(user_context=ctx(), query="keeping up beyond", media_type="video")
    with patch.object(vs, "_setting", new=_settings({})), \
            patch.object(video_handler, "search_youtube_entries",
                         new=AsyncMock(return_value=[{"url": GOSSIP.ident, "title": GOSSIP.title,
                                                      "channel": GOSSIP.by, "age_limit": None}])), \
            patch.object(video_handler, "inspect_video", new=AsyncMock(return_value={"age_limit": 0})), \
            patch.object(video_handler, "download_video_progressive", new=AsyncMock()) as download:
        result = await media_handler.play_video(req, "media_player.office_tv", req.user_context)
    assert result.detail["route"] == "ask" and download.await_count == 0
