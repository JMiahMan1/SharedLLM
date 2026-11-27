"""YouTube at a speaker (its audio, or which TV), and the formats a screen is sent.

What happened (2026-11-24, at a satellite speaker): "Play <song> on YouTube" named no screen,
and the request crashed the gateway; on a named TV the video was found but yt-dlp refused the
old single-file formats ("Requested format is not available"). Every title, channel, player
and URL here is made up; yt-dlp is faked (its real format selector is used only when the
package is installed, on made-up format lists, with no network).
"""
from __future__ import annotations

import asyncio
import json
import pathlib
from unittest.mock import AsyncMock, patch

import pytest

from services.execution import media_finder as mf
from services.execution import video_safety as vs
from services.execution.handlers import media as media_handler
from services.execution.handlers import video as video_handler
from services.execution.media_finder import Candidate as C
from services.execution.schemas import ExecutionResult, MediaPlayRequest, UserContext

SPEAKER = "media_player.den_satellite_speaker"
SPEAKER_MA = "media_player.den_satellite_speaker_ma"
DEN_TV = "media_player.den_tv"
PLAYROOM_TV = "media_player.playroom_tv"
SONG = C("web_video", "video", "Fake Band - Moon Garden (Official Audio)", "https://youtube.test/watch?v=moon1",
         by="Fake Band")
OTHER = C("web_video", "video", "Ten Facts About Gardens", "https://youtube.test/watch?v=facts1", by="Fact Channel")


def ctx(**kw) -> UserContext:
    return UserContext(**{"user": "tester", "ha_url": "http://hub.test:8123", "ha_token": "hub-token", **kw})


def _req(query: str, **kw) -> MediaPlayRequest:
    return MediaPlayRequest(user_context=ctx(), query=query, media_type="video", search_everywhere=True, **kw)


def _settings(values: dict | None = None):
    async def read(key: str) -> str:
        return (values or {}).get(key, "")
    return read


async def _at_speaker(req, found, *, facts=None, settings=None, screens=None):
    """find_and_play on a satellite speaker (no screen), with fake YouTube results and site facts."""
    vs._checked.clear()
    facts = facts or {}

    async def inspect(url):
        f = facts.get(url, {})
        if isinstance(f, Exception):
            raise f
        return {"title": "", "channel": "", "age_limit": 0, **f}

    fresh = [C(c.source, c.kind, c.title, c.ident, by=c.by, extra=dict(c.extra)) for c in found]
    played = AsyncMock(side_effect=lambda cand, *_a, **_k: ExecutionResult.ok(f"played {cand.ident}", service="t"))
    with patch.object(vs, "_setting", new=_settings(settings)), \
            patch.object(mf, "_setting", new=_settings(settings)), \
            patch.object(video_handler, "inspect_video", new=inspect), \
            patch.object(mf, "target_facts", new=AsyncMock(return_value=(False, "Den Satellite Speaker"))), \
            patch.object(mf, "search_web_video", new=AsyncMock(return_value=fresh)) as searched, \
            patch.object(mf, "screens_listed", new=AsyncMock(return_value=screens or [])), \
            patch.object(mf, "discover_sources", new=AsyncMock(side_effect=AssertionError("libraries searched"))), \
            patch.object(mf, "play_candidate", new=played):
        result = await mf.find_and_play(req, SPEAKER, req.user_context)
    return result, played, searched


# --------------------------------------------------------------------------- #
# "Play X on YouTube" at a speaker: its audio                                  #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_a_strong_youtube_match_plays_its_audio_on_the_speaker():
    result, played, searched = await _at_speaker(_req("Moon Garden on YouTube"), [SONG])
    assert result.message == f"played {SONG.ident}"
    assert searched.await_args.args[0] == "Moon Garden"
    assert played.await_args.kwargs["audio_only"] is True
    assert played.await_args.args[2] == SPEAKER


@pytest.mark.asyncio
async def test_a_weak_youtube_match_at_a_speaker_is_asked_about_not_played():
    result, played, _ = await _at_speaker(_req("Moon Garden on YouTube"), [OTHER])
    assert played.await_count == 0
    assert result.detail["route"] == "ask"
    assert result.message == "I found 'Ten Facts About Gardens' on YouTube. Play its audio?"
    [option] = result.detail["choice"]["options"]
    assert option["pick"]["ident"] == OTHER.ident and result.detail["choice"]["entity_id"] == SPEAKER


@pytest.mark.asyncio
async def test_the_family_rules_apply_to_the_audio_too():
    adult = C("web_video", "video", "Moon Garden", "https://youtube.test/watch?v=adult1")
    result, played, _ = await _at_speaker(_req("Moon Garden on YouTube"), [adult], facts={adult.ident: {"age_limit": 18}})
    assert played.await_count == 0 and result.status == "FAILURE"
    assert result.detail["reason"] == "age_restricted"
    assert result.message == "I didn't find Moon Garden on YouTube that's suitable to play here."

    result, played, _ = await _at_speaker(_req("Moon Garden on YouTube"), [SONG],
                                          settings={vs.BLOCKLIST_SETTING: "Fake Band"})
    assert played.await_count == 0 and result.detail["reason"] == "blocked"


@pytest.mark.asyncio
async def test_no_youtube_search_when_voice_video_search_is_off():
    result, played, searched = await _at_speaker(_req("Moon Garden on YouTube"), [SONG],
                                                 settings={mf.VIDEO_SEARCH_SETTING: "off"})
    assert played.await_count == 0 and searched.await_count == 0
    assert result.detail["reason"] == "video_search_off"


# --------------------------------------------------------------------------- #
# "Play the video of X" at a speaker: which TV                                 #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_a_video_asked_for_at_a_speaker_asks_which_tv():
    result, played, searched = await _at_speaker(
        _req("the video Moon Garden on YouTube"), [SONG], screens=[(DEN_TV, "Den TV"), (PLAYROOM_TV, "Playroom TV")])
    assert played.await_count == 0 and searched.await_count == 0
    assert result.message == "Which TV should I put it on, the Den TV or the Playroom TV?"
    choice = result.detail["choice"]
    assert choice["query"] == "the video Moon Garden on YouTube" and choice["entity_id"] == SPEAKER
    assert [(o["entity_id"], o["label"]) for o in choice["options"]] == [(DEN_TV, "Den TV"), (PLAYROOM_TV, "Playroom TV")]
    assert "den" in choice["options"][0]["words"] and "tv" not in choice["options"][0]["words"]


@pytest.mark.asyncio
async def test_one_tv_is_offered_with_a_yes():
    result, _played, _ = await _at_speaker(_req("watch Moon Garden on YouTube"), [SONG], screens=[(DEN_TV, "Den TV")])
    assert result.message == "Should I put it on the Den TV?"
    [option] = result.detail["choice"]["options"]
    assert option["entity_id"] == DEN_TV and "yes" in option["words"]


@pytest.mark.asyncio
async def test_no_tv_at_all_says_so():
    result, _played, _ = await _at_speaker(_req("the video Moon Garden"), [SONG], screens=[])
    assert result.status == "FAILURE" and result.detail["reason"] == "no_screen"
    assert result.message == "Den Satellite Speaker can't show video, and I didn't find a TV to put Moon Garden on."


def test_what_counts_as_asking_for_the_picture():
    assert mf.asks_for_picture("the video Moon Garden on YouTube")
    assert mf.asks_for_picture("watch Moon Garden")
    assert not mf.asks_for_picture("Moon Garden on YouTube")
    assert mf.names_youtube("Moon Garden on YouTube") and not mf.names_youtube("Moon Garden")
    assert mf.screen_words("Den TV Chrome") == ["den tv chrome", "den"]


# --------------------------------------------------------------------------- #
# Playing the audio                                                            #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_play_candidate_sends_a_speaker_the_audio_and_says_so():
    cand = C("web_video", "video", SONG.title, SONG.ident, by=SONG.by, extra={"title_known": True})
    audio = AsyncMock(return_value=ExecutionResult.ok("Playing the audio.", service="media_play"))
    with patch.object(media_handler, "play_web_audio", new=audio), \
            patch.object(media_handler, "play_video", new=AsyncMock(side_effect=AssertionError("video on a speaker"))):
        result = await mf.play_candidate(cand, _req("Moon Garden on YouTube"), SPEAKER, ctx(),
                                         "Den Satellite Speaker", audio_only=True)
    assert audio.await_args.args[:2] == (SONG.ident, SPEAKER)
    assert result.message == "Playing the audio of 'Fake Band - Moon Garden (Official Audio)' from YouTube on Den Satellite Speaker."


@pytest.mark.asyncio
async def test_a_yes_to_the_audio_question_plays_audio_on_the_speaker():
    req = _req("Moon Garden on YouTube", pick=OTHER.as_pick())
    played = AsyncMock(return_value=ExecutionResult.ok("ok", service="t"))
    with patch.object(mf, "target_facts", new=AsyncMock(return_value=(False, "Den Satellite Speaker"))), \
            patch.object(mf, "discover_sources", new=AsyncMock(return_value=[mf.Source("web_video", "YouTube", True)])), \
            patch.object(mf, "play_candidate", new=played):
        await mf.find_and_play(req, SPEAKER, req.user_context)
    assert played.await_args.kwargs["audio_only"] is True


def _audio_patches(*, age_limit=0, ma_twin=SPEAKER_MA, is_ma=False):
    calls: list = []

    async def call_service(_url, _token, domain, service, entity_id, data=None, **_kw):
        calls.append((domain, service, entity_id, data))
        return {"ok": True}

    return calls, [
        patch.object(vs, "_setting", new=_settings()),
        patch.object(video_handler, "inspect_video", new=AsyncMock(return_value={"age_limit": age_limit})),
        patch.object(video_handler, "extract_audio_stream_url",
                     new=AsyncMock(return_value=("https://cdn.test/audio.m4a", "Moon Garden"))),
        patch.object(media_handler, "resolve_mass_entity", new=AsyncMock(return_value=ma_twin)),
        patch.object(media_handler, "_is_ma_entity", new=AsyncMock(return_value=is_ma)),
        patch.object(media_handler.ha_client, "call_service", new=call_service),
    ]


async def _play_audio(**kw):
    vs._checked.clear()
    calls, patches = _audio_patches(**kw)
    for p in patches:
        p.start()
    try:
        result = await media_handler.play_web_audio(SONG.ident, SPEAKER, ctx())
    finally:
        for p in patches:
            p.stop()
    return result, calls


@pytest.mark.asyncio
async def test_the_audio_goes_through_the_speakers_music_assistant_player():
    result, calls = await _play_audio()
    assert result.status == "SUCCESS"
    assert calls == [("music_assistant", "play_media", SPEAKER_MA,
                      {"media_id": "https://cdn.test/audio.m4a", "enqueue": "replace"})]


@pytest.mark.asyncio
async def test_a_speaker_without_music_assistant_is_sent_the_stream():
    result, calls = await _play_audio(ma_twin=SPEAKER, is_ma=False)
    assert result.status == "SUCCESS"
    assert calls == [("media_player", "play_media", SPEAKER,
                      {"media_content_id": "https://cdn.test/audio.m4a", "media_content_type": "music"})]


@pytest.mark.asyncio
async def test_the_audio_is_screened_before_it_plays():
    result, calls = await _play_audio(age_limit=18)
    assert result.status == "FAILURE" and result.detail["reason"] == "age_restricted"
    assert calls == []


# --------------------------------------------------------------------------- #
# The formats a screen is sent                                                 #
# --------------------------------------------------------------------------- #

def test_the_format_chain_prefers_merged_h264_and_aac_and_never_asks_only_for_22_or_18():
    chain = video_handler.VIDEO_FORMAT.split("/")
    assert chain[0] == "bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]"
    assert chain[-1] == "b"
    assert "22" not in chain and "18" not in chain
    assert video_handler.video_format_args() == ["-f", video_handler.VIDEO_FORMAT, "--merge-output-format", "mp4"]


class _FakeYtDlp:
    """yt-dlp on the command line: knows one video's formats and answers like the real one."""

    def __init__(self, tmp_path: pathlib.Path, offered: set[str]):
        self.tmp_path, self.offered, self.commands = tmp_path, offered, []

    async def __call__(self, *cmd, **_kw):
        self.commands.append(cmd)
        proc = AsyncMock()
        proc.kill = lambda: None
        if "--dump-json" in cmd:
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(json.dumps({"title": "Moon Garden"}).encode(), b""))
            return proc
        wanted = cmd[cmd.index("-f") + 1].split("/")
        # The first alternative whose parts are all offered, as yt-dlp chooses.
        pick = next((alt for alt in wanted if all(part.split("[")[0] in self.offered for part in alt.split("+"))), None)
        if pick is None:
            proc.returncode = 1
            proc.communicate = AsyncMock(return_value=(b"", b"ERROR: [youtube] moon1: Requested format is not available"))
        else:
            pathlib.Path(cmd[cmd.index("-o") + 1]).write_bytes(b"\0" * 64)
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc


@pytest.mark.parametrize("fn", ["download_video_progressive", "download_video_for_roku", "download_video"])
@pytest.mark.asyncio
async def test_a_video_offered_only_as_separate_streams_downloads(monkeypatch, tmp_path, fn):
    """The live failure: no single-file format, only DASH video and audio (bv*/ba stand for them)."""
    fake = _FakeYtDlp(tmp_path, offered={"bv*", "ba"})
    monkeypatch.setattr(video_handler, "TEMP_MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(video_handler, "_ensure_youtube_cookies", AsyncMock(return_value=None))
    monkeypatch.setattr(video_handler.asyncio, "create_subprocess_exec", fake)
    media_id, title = await asyncio.wait_for(getattr(video_handler, fn)("https://youtube.test/watch?v=moon1"), 5)
    assert media_id and title == "Moon Garden"
    [download] = [c for c in fake.commands if "-f" in c]
    assert download[download.index("-f") + 1] == video_handler.VIDEO_FORMAT
    assert download[download.index("--merge-output-format") + 1] == "mp4"


@pytest.mark.asyncio
async def test_the_old_selector_fails_where_the_chain_does_not(monkeypatch, tmp_path):
    fake = _FakeYtDlp(tmp_path, offered={"bv*", "ba"})
    monkeypatch.setattr(video_handler.asyncio, "create_subprocess_exec", fake)
    old = await fake("yt-dlp", "-f", "22/18/best[ext=mp4][height<=720]", "-o", str(tmp_path / "old.mp4"), "u")
    assert old.returncode == 1
    new = await fake("yt-dlp", *video_handler.video_format_args(), "-o", str(tmp_path / "new.mp4"), "u")
    assert new.returncode == 0


def _formats(*specs):
    out = []
    for fid, ext, vcodec, acodec, height, tbr in specs:
        out.append({"format_id": fid, "ext": ext, "vcodec": vcodec, "acodec": acodec, "height": height,
                    "width": int(height * 16 / 9) if height else None, "tbr": tbr, "protocol": "https",
                    "url": f"https://cdn.test/{fid}"})
    return out


DASH_H264 = ("137", "mp4", "avc1.640028", "none", 1080, 528)
DASH_H264_4K = ("401", "mp4", "avc1.640033", "none", 2160, 9000)
DASH_VP9 = ("248", "webm", "vp9", "none", 1080, 314)
AUDIO_M4A = ("140", "m4a", "none", "mp4a.40.2", None, 130)
AUDIO_OPUS = ("251", "webm", "none", "opus", None, 139)
SINGLE_18 = ("18", "mp4", "avc1.42001E", "mp4a.40.2", 360, 187)
SINGLE_WEBM = ("43", "webm", "vp8", "vorbis", 360, 500)


@pytest.mark.parametrize("specs, chosen", [
    ((DASH_H264, DASH_H264_4K, DASH_VP9, AUDIO_M4A, AUDIO_OPUS, SINGLE_18), "137+140"),  # H.264 + AAC, at most 1080p
    ((DASH_H264, AUDIO_OPUS, SINGLE_18), "18"),        # no AAC to pair: the single-file H.264/AAC MP4
    ((DASH_VP9, AUDIO_OPUS), "248+251"),               # no H.264 at all: any pair, merged to MP4
    ((SINGLE_WEBM,), "43"),                            # nothing else: whatever there is
])
def test_yt_dlps_own_selector_walks_the_chain(specs, chosen):
    yt_dlp = pytest.importorskip("yt_dlp")
    with yt_dlp.YoutubeDL({"quiet": True, "merge_output_format": "mp4"}) as ydl:
        select = ydl.build_format_selector(video_handler.VIDEO_FORMAT)
        formats = sorted(_formats(*specs), key=lambda f: (f["height"] or 0, f["tbr"]))  # worst first, as yt-dlp sorts
        incomplete = all(f["vcodec"] == "none" for f in formats) or all(f["acodec"] == "none" for f in formats)
        picked = list(select({"formats": formats, "has_merged_format": True, "incomplete_formats": incomplete}))
    assert picked and picked[0]["format_id"] == chosen
