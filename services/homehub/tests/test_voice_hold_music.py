"""Hold music and the ask-later line: a long wait is filled with a playful line and gentle music, and an
answer that never comes (or a Jarvis too busy to give one) is met with a plain "ask me again" line."""
from __future__ import annotations

import asyncio
import io
import itertools
import math
import wave

import numpy as np
import pytest

from services.homehub import tts, voice_pipeline
from services.homehub.tests.test_voice_hold import HOLD, SlowJarvis
from services.homehub.tests.test_voice_pipeline import (  # fixtures and stand-ins, not tests
    E,
    _silence,
    _tone,
    env,
    loudness_alone,
    make_driver,
    run_turn,
    settings_from_the_test,
)
from services.homehub.voice_pipeline import PipelineError

__all__ = ["env", "loudness_alone", "settings_from_the_test"]  # pytest finds the imported fixtures by name

AUDIO = [_tone(0.6), _silence(1.0)]
ANSWER = ["It will be sunny tomorrow.", "Highs near seventy."]
MUSIC = "<music>"
LINE = voice_pipeline.DEFAULT_TIMEOUT_LINE


@pytest.fixture(autouse=True)
def quick_hold(monkeypatch):
    monkeypatch.setattr(voice_pipeline, "_HOLD_AUDIO", {})
    monkeypatch.setattr(voice_pipeline, "_LAST_HOLD_LINE", None)
    monkeypatch.setattr(voice_pipeline, "HOLD_MUSIC_CHUNK_S", 0.1)
    monkeypatch.setattr(voice_pipeline, "HOLD_MUSIC_LEAD_S", 0.2)
    monkeypatch.setenv("HOMEHUB_VOICE_HOLD_AFTER_S", "0.05")
    monkeypatch.setenv("HOMEHUB_VOICE_HOLD_MUSIC_AFTER_S", "0.05")
    for name in ("HOMEHUB_VOICE_HOLD_PHRASE", "HOMEHUB_VOICE_HOLD_MUSIC", "HOMEHUB_VOICE_HOLD_LINES",
                 "HOMEHUB_VOICE_TIMEOUT_LINE"):
        monkeypatch.delenv(name, raising=False)


class Music(SlowJarvis):
    """Records music chunks too (``append_pcm``), and can be slow to synthesise the answer itself."""

    def __init__(self, answer_synth_s=0.0, **kw):
        super().__init__(**kw)
        self.answer_synth_s = answer_synth_s
        self.music_pcm: list[bytes] = []

    def open_stream(self):
        url, stream = super().open_stream()
        append_pcm = stream.append_pcm

        def recording(pcm):
            self.appended.append(MUSIC)
            self.music_pcm.append(pcm)
            return append_pcm(pcm)

        stream.append_pcm = recording
        return url, stream

    async def synth(self, text, voice):
        if text in ANSWER and self.answer_synth_s:
            await asyncio.sleep(self.answer_synth_s)
        return await super().synth(text, voice)


def _summary(caplog) -> str:
    return next(r.getMessage() for r in caplog.records if " run " in r.getMessage() and "; ended by " in r.getMessage())


async def test_a_long_wait_gets_the_phrase_a_line_and_music_then_the_answer_in_one_stream(env, monkeypatch, caplog):
    be = Music(delay=0.8, answer_synth_s=0.2).install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    client = drv._client
    caplog.set_level("INFO", logger="homehub.voice")
    await run_turn(drv, client, AUDIO)
    said = be.appended
    assert said[0] == HOLD and said[1] in voice_pipeline.DEFAULT_HOLD_LINES
    first = said.index(ANSWER[0])
    assert said[first:] == ANSWER  # the answer whole, after the music, nothing in between its sentences
    music = said[2:first]
    assert len(music) >= 3 and set(music) == {MUSIC}  # several chunks while the answer was being made
    assert MUSIC not in said[first:]  # the music stopped before the answer's first sentence
    assert len(be.streams) == 1 and be.streams[0][1].closed
    assert client.kinds().count(E.VOICE_ASSISTANT_INTENT_PROGRESS) == 1
    # The last music piece is the fade: 0.3 s, ending in silence.
    fade = np.frombuffer(be.music_pcm[-1], "<i2")
    assert len(fade) == int(voice_pipeline.HOLD_MUSIC_FADE_S * 24000) and abs(int(fade[-1])) < 50
    run = drv.voice.last_run
    assert run.outcome == "ok" and run.reply_mode == "stream" and not run.timeout_line
    expected_ms = round(sum(len(p) for p in be.music_pcm) / 2 / 24000 * 1000)
    assert run.music_ms == expected_ms > 300
    line = _summary(caplog)
    assert f"music={run.music_ms}ms" in line and "hold=" in line and "timeout_line" not in line
    await drv.async_unload()


@pytest.mark.parametrize(("name", "value"), [("HOMEHUB_VOICE_HOLD_MUSIC", "0"),
                                             ("HOMEHUB_VOICE_HOLD_MUSIC_AFTER_S", "0")])
async def test_music_turned_off_means_no_line_and_no_music(env, monkeypatch, caplog, name, value):
    monkeypatch.setenv(name, value)
    be = Music(delay=0.6).install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    caplog.set_level("INFO", logger="homehub.voice")
    await run_turn(drv, drv._client, AUDIO)
    assert be.appended == [HOLD, *ANSWER]  # the holding phrase still, nothing more
    assert drv.voice.last_run.music_ms == 0 and "music=" not in _summary(caplog)
    await drv.async_unload()


async def test_a_fast_answer_has_no_phrase_line_or_music(env, monkeypatch, caplog):
    monkeypatch.setenv("HOMEHUB_VOICE_HOLD_AFTER_S", "5")
    be = Music(delay=0.0).install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    caplog.set_level("INFO", logger="homehub.voice")
    await run_turn(drv, drv._client, AUDIO)
    assert be.appended == ANSWER and be.music_pcm == []
    line = _summary(caplog)
    assert "hold=" not in line and "music=" not in line and "timeout_line" not in line
    await drv.async_unload()


async def test_an_answer_ready_before_the_music_has_no_line_or_music(env, monkeypatch):
    monkeypatch.setenv("HOMEHUB_VOICE_HOLD_MUSIC_AFTER_S", "5")
    be = Music(delay=0.3).install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, AUDIO)
    assert be.appended == [HOLD, *ANSWER]
    await drv.async_unload()


def test_hold_lines_are_never_said_twice_in_a_row(monkeypatch):
    monkeypatch.setattr(voice_pipeline, "_LAST_HOLD_LINE", None)
    picks = [voice_pipeline.pick_hold_line(voice_pipeline.DEFAULT_HOLD_LINES) for _ in range(300)]
    assert all(a != b for a, b in itertools.pairwise(picks))
    assert set(picks) == set(voice_pipeline.DEFAULT_HOLD_LINES)  # and every one gets its turn
    two = [voice_pipeline.pick_hold_line(("A.", "B.")) for _ in range(20)]
    assert two in (["A.", "B."] * 10, ["B.", "A."] * 10)
    assert voice_pipeline.pick_hold_line(("Only.",)) == voice_pipeline.pick_hold_line(("Only.",)) == "Only."
    assert voice_pipeline.pick_hold_line(()) is None


def test_the_default_lines_are_short_plain_ascii():
    lines = voice_pipeline.DEFAULT_HOLD_LINES
    assert 6 <= len(lines) <= 10 and len(set(lines)) == len(lines)
    assert all(line.isascii() and len(line.split()) <= 16 for line in lines)
    assert "Please hold. Your question is very important to us." in lines
    assert "Hold please, I am consulting the scrolls." in lines


def test_the_settings_read_hold_music_and_the_timeout_line(monkeypatch):
    s = voice_pipeline.VoiceSettings.from_env()
    assert s.hold_music and s.hold_music_after_s == 0.05
    monkeypatch.delenv("HOMEHUB_VOICE_HOLD_MUSIC_AFTER_S")
    s = voice_pipeline.VoiceSettings.from_env()
    assert s.hold_music_after_s == 6.0 and s.hold_lines == voice_pipeline.DEFAULT_HOLD_LINES
    assert s.timeout_line == "I'm still tied up with something. Please ask me again in a minute."
    pub = s.public()
    assert pub["hold_music"] is True and pub["hold_music_after_s"] == 6.0
    assert pub["hold_lines"] == list(voice_pipeline.DEFAULT_HOLD_LINES) and pub["timeout_line"] == s.timeout_line
    monkeypatch.setenv("HOMEHUB_VOICE_HOLD_MUSIC", "off")
    monkeypatch.setenv("HOMEHUB_VOICE_HOLD_LINES", " Hold on. | |Nearly there. ")
    monkeypatch.setenv("HOMEHUB_VOICE_TIMEOUT_LINE", "Try me later.")
    s = voice_pipeline.VoiceSettings.from_env()
    assert not s.hold_music and s.hold_lines == ("Hold on.", "Nearly there.") and s.timeout_line == "Try me later."
    monkeypatch.setenv("HOMEHUB_VOICE_HOLD_MUSIC_AFTER_S", "-1")
    with pytest.raises(voice_pipeline.HubError, match="HOLD_MUSIC_AFTER_S"):
        voice_pipeline.VoiceSettings.from_env()


@pytest.mark.parametrize("rate", [24000, 22050])
def test_the_hold_music_is_deterministic_at_the_streams_format_and_quiet(monkeypatch, rate):
    monkeypatch.setattr(voice_pipeline, "_HOLD_MUSIC", {})
    loop = voice_pipeline.hold_music_loop(rate)
    assert voice_pipeline.hold_music_loop(rate) is loop  # cached
    monkeypatch.setattr(voice_pipeline, "_HOLD_MUSIC", {})
    assert voice_pipeline.hold_music_loop(rate) == loop  # and the same every time it is made
    seconds = voice_pipeline.HOLD_CHORD_S * len(voice_pipeline._HOLD_CHORDS)
    assert len(loop) == int(voice_pipeline.HOLD_CHORD_S * rate) * len(voice_pipeline._HOLD_CHORDS) * 2
    assert len(loop) / 2 / rate == pytest.approx(seconds, abs=1e-3)
    pcm = np.frombuffer(loop, "<i2").astype(float)
    rms_db = 20 * math.log10(math.sqrt(float(np.mean(pcm ** 2))) / 32767)
    assert -25.0 <= rms_db <= -23.0  # about -24 dBFS, well under speech
    assert 20 * math.log10(float(np.max(np.abs(pcm))) / 32767) < -10  # and nowhere near clipping
    steps = np.abs(np.diff(np.concatenate([pcm, pcm[:1]])))
    assert steps[-1] <= np.percentile(steps, 99.9)  # the seam where it loops round is no click


async def test_music_goes_into_the_stream_at_its_own_rate_with_no_gaps():
    stream = tts.ClipStream()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x01\x00" * 2400)
    stream.append(buf.getvalue())
    before = len(stream.parts)
    assert stream.append_pcm(b"\x02\x00" * 4800) == pytest.approx(0.2)
    assert len(stream.parts) == before + 1 and stream.seconds == pytest.approx(0.3)  # no pause added
    stream.close()
    with pytest.raises(voice_pipeline.HubError):
        stream.append_pcm(b"\x00\x00")
    with pytest.raises(voice_pipeline.HubError, match="no sample rate"):
        tts.ClipStream().append_pcm(b"\x00\x00")


async def test_a_timeout_says_the_ask_later_line_into_the_stream_and_still_logs_it(env, monkeypatch, caplog):
    monkeypatch.setenv("HOMEHUB_VOICE_JARVIS_TIMEOUT_S", "0.7")
    monkeypatch.setattr(voice_pipeline, "JARVIS_GUARD_S", 0.0)
    be = Music(delay=5.0).install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    client = drv._client
    caplog.set_level("INFO", logger="homehub.voice")
    await run_turn(drv, client, AUDIO)
    said = be.appended
    assert said[0] == HOLD and said[1] in voice_pipeline.DEFAULT_HOLD_LINES and MUSIC in said
    assert said[-1] == LINE and said[-2] == MUSIC and said.count(LINE) == 1  # music faded, then the line
    assert be.streams[0][1].closed and client.kinds().count(E.VOICE_ASSISTANT_INTENT_PROGRESS) == 1
    kinds = client.kinds()
    assert E.VOICE_ASSISTANT_ERROR not in kinds and kinds[-2:] == [E.VOICE_ASSISTANT_TTS_END,
                                                                  E.VOICE_ASSISTANT_RUN_END]
    assert dict(client.events)[E.VOICE_ASSISTANT_TTS_START] == {"text": LINE}
    run = drv.voice.last_run
    assert run.outcome == "error" and run.error["code"] == "intent-timeout" and run.timeout_line
    assert run.music_ms > 0
    summary = _summary(caplog)
    assert "(intent-timeout)" in summary and summary.endswith("timeout_line=1") and "music=" in summary
    assert any("did not answer within" in r.getMessage() and "intent-timeout" in r.getMessage()
               for r in caplog.records)
    await drv.async_unload()


class Busy(Music):
    def __init__(self, error, **kw):
        super().__init__(**kw)
        self.error = error

    async def ask_stream(self, settings, text, context, on_sentence):
        await asyncio.sleep(self.delay)
        raise self.error


@pytest.mark.parametrize("delay", [0.0, 0.3])
async def test_a_busy_jarvis_gets_the_ask_later_line(env, monkeypatch, caplog, delay):
    monkeypatch.setenv("HOMEHUB_VOICE_HOLD_MUSIC_AFTER_S", "5")  # the holding phrase only, no music yet
    be = Busy(PipelineError("intent-busy", "Jarvis is busy (HTTP 503): queue_timeout"), delay=delay).install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    caplog.set_level("INFO", logger="homehub.voice")
    await run_turn(drv, drv._client, AUDIO)
    run = drv.voice.last_run
    if delay:  # the holding phrase had started the stream: the line follows it there
        assert be.appended == [HOLD, LINE] and be.spoken == [] and run.reply_mode == "stream"
    else:  # nothing streamed yet: the line is a whole reply
        assert be.appended == [] and be.spoken == [LINE] and run.reply_mode == "whole"
    assert run.outcome == "error" and run.error["code"] == "intent-busy" and run.timeout_line
    assert _summary(caplog).endswith("timeout_line=1")
    await drv.async_unload()


async def test_other_failures_are_not_met_with_the_ask_later_line(env, monkeypatch):
    be = Busy(PipelineError("intent-failed", "Jarvis at x is unreachable"), delay=0.0).install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, AUDIO)
    assert be.spoken == [] and be.appended == [] and not drv.voice.last_run.timeout_line
    kinds = drv._client.kinds()
    assert kinds[-2:] == [E.VOICE_ASSISTANT_ERROR, E.VOICE_ASSISTANT_RUN_END]
    await drv.async_unload()


@pytest.mark.parametrize(("status", "data"), [
    (503, {"detail": "All slots are busy"}),
    (429, {"detail": "Too many requests"}),
    (500, {"error": "llama-server: queue_timeout after 120 s"}),
    (502, {"status": "ERROR", "code": "queue_timeout", "message": "the model queue timed out"}),
])
def test_busy_answers_from_the_gateway_are_busy_errors(status, data):
    with pytest.raises(PipelineError) as err:
        voice_pipeline._reply_from_answer(status, data)
    assert err.value.code == "intent-busy"


def test_other_gateway_errors_stay_failures_and_the_offline_answer_is_still_said():
    with pytest.raises(PipelineError) as err:
        voice_pipeline._reply_from_answer(500, {"detail": "boom"})
    assert err.value.code == "intent-failed"
    offline = voice_pipeline._reply_from_answer(503, {"offline": True, "message": "My thinking brain is offline."})
    assert str(offline) == "My thinking brain is offline."


async def test_a_queue_timeout_in_the_streamed_answer_is_busy():
    async def lines():
        yield b'{"error": "queue_timeout: no slot within 120 s"}\n'

    async def on_sentence(text, voice):
        raise AssertionError("nothing to say")

    with pytest.raises(PipelineError) as err:
        await voice_pipeline.read_speech_stream(lines(), on_sentence)
    assert err.value.code == "intent-busy"

    async def done_error():
        yield b'{"done": true, "message": {"content": "[ERROR]: queue_timeout"}}\n'

    with pytest.raises(PipelineError) as err:
        await voice_pipeline.read_speech_stream(done_error(), on_sentence)
    assert err.value.code == "intent-busy"
