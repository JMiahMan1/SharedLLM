"""The holding phrase: a slow answer starts the streamed reply with "One moment." so the node does not go dark."""
from __future__ import annotations

import asyncio

import pytest

from services.homehub import voice_pipeline
from services.homehub.tests.test_voice_pipeline import (  # fixtures and stand-ins, not tests
    URL,
    E,
    _silence,
    _tone,
    _wav,
    env,
    loudness_alone,
    make_driver,
    run_turn,
    settings_from_the_test,
)
from services.homehub.tests.test_voice_streaming import StreamBackends

__all__ = ["env", "loudness_alone", "settings_from_the_test"]  # pytest finds the imported fixtures by name

HOLD = "One moment."
AUDIO = [_tone(0.6), _silence(1.0)]


@pytest.fixture(autouse=True)
def fresh_hold_cache(monkeypatch):
    monkeypatch.setattr(voice_pipeline, "_HOLD_AUDIO", {})
    monkeypatch.setenv("HOMEHUB_VOICE_HOLD_AFTER_S", "0.05")
    monkeypatch.delenv("HOMEHUB_VOICE_HOLD_PHRASE", raising=False)


class SlowJarvis(StreamBackends):
    """Jarvis taking ``delay`` seconds before its first sentence; ``hold_synth_s`` is how long the phrase takes."""

    def __init__(self, delay=0.3, hold_synth_s=0.0, **kw):
        super().__init__(**kw)
        self.delay, self.hold_synth_s = delay, hold_synth_s
        self.appended: list[str] = []  # what went into the stream, in order
        self.hold_started = 0  # how many times the holding phrase began to be made
        self._audio: dict[bytes, str] = {}

    def open_stream(self):
        url, stream = super().open_stream()
        append = stream.append

        def recording(audio):
            self.appended.append(self._audio[audio])
            return append(audio)

        stream.append = recording
        return url, stream

    async def ask_stream(self, settings, text, context, on_sentence):
        await asyncio.sleep(self.delay)
        return await super().ask_stream(settings, text, context, on_sentence)

    async def synth(self, text, voice):
        if text == HOLD:
            self.hold_started += 1
            if self.hold_synth_s:
                await asyncio.sleep(self.hold_synth_s)
        await super().synth(text, voice)
        audio = _wav(24000, 0.1 + 0.01 * len(self._audio))  # each text its own bytes, to tell them apart
        self._audio.setdefault(audio, text)
        return audio


async def test_a_slow_answer_starts_with_the_holding_phrase_in_the_same_stream(env, monkeypatch, caplog):
    be = SlowJarvis().install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    client = drv._client
    caplog.set_level("INFO", logger="homehub.voice")
    await run_turn(drv, client, AUDIO)
    assert be.appended == [HOLD, "It will be sunny tomorrow.", "Highs near seventy."]
    assert len(be.streams) == 1 and be.streams[0][1].closed  # one stream, opened once, ended once
    kinds = client.kinds()
    assert kinds.count(E.VOICE_ASSISTANT_INTENT_PROGRESS) == 1  # the node was told to play it once
    assert E.VOICE_ASSISTANT_TTS_STREAM_START not in kinds  # never a second audio path
    assert kinds.index(E.VOICE_ASSISTANT_INTENT_START) < kinds.index(E.VOICE_ASSISTANT_INTENT_PROGRESS)
    assert dict(client.events)[E.VOICE_ASSISTANT_TTS_END] == {"url": be.streams[0][0]}
    run = drv.voice.last_run
    assert run.outcome == "ok" and run.reply_mode == "stream"
    t = run.timings_ms()
    assert 40 <= t["hold"] < t["intent"]
    assert run.marks["hold"] < run.marks["first_audio"]  # first_audio is still the answer's own
    line = next(r.getMessage() for r in caplog.records if " run " in r.getMessage() and ": ok" in r.getMessage())
    assert f"hold={t['hold']}ms" in line
    await drv.async_unload()


async def test_a_fast_answer_has_no_holding_phrase(env, monkeypatch, caplog):
    monkeypatch.setenv("HOMEHUB_VOICE_HOLD_AFTER_S", "5")
    be = SlowJarvis(delay=0.0).install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    caplog.set_level("INFO", logger="homehub.voice")
    await run_turn(drv, drv._client, AUDIO)
    assert HOLD not in be.synthesized and be.appended == ["It will be sunny tomorrow.", "Highs near seventy."]
    assert "hold" not in drv.voice.last_run.timings_ms()
    assert not any("hold=" in r.getMessage() for r in caplog.records)
    await drv.async_unload()


async def test_an_answer_that_arrives_while_the_phrase_is_made_is_never_followed_by_it(env, monkeypatch):
    be = SlowJarvis(delay=0.1, hold_synth_s=0.4).install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, AUDIO)
    assert be.hold_started == 1  # it was being made when the answer came
    assert be.appended == ["It will be sunny tomorrow.", "Highs near seventy."]  # and never said
    assert drv._client.kinds().count(E.VOICE_ASSISTANT_INTENT_PROGRESS) == 1
    assert "hold" not in drv.voice.last_run.marks
    await drv.async_unload()


async def test_a_shortcut_answer_after_the_phrase_follows_it_in_the_stream(env, monkeypatch):
    """The node is already playing the stream, so a whole answer goes into it rather than a second clip."""
    be = SlowJarvis(sentences=(), rest="", whole="The lamp is on.").install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, AUDIO)
    assert be.appended == [HOLD, "The lamp is on."] and be.spoken == []
    assert dict(drv._client.events)[E.VOICE_ASSISTANT_TTS_END] == {"url": be.streams[0][0]}
    assert drv.voice.last_run.reply_mode == "stream" and drv.voice.last_run.outcome == "ok"
    await drv.async_unload()


async def test_hold_after_zero_turns_it_off(env, monkeypatch):
    monkeypatch.setenv("HOMEHUB_VOICE_HOLD_AFTER_S", "0")
    be = SlowJarvis().install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, AUDIO)
    assert HOLD not in be.synthesized and be.appended[0] == "It will be sunny tomorrow."
    await drv.async_unload()


async def test_without_streamed_replies_nothing_is_held(env, monkeypatch):
    monkeypatch.setenv("HOMEHUB_VOICE_STREAM", "0")
    be = SlowJarvis().install(monkeypatch)
    be.ask_delay = 0.3  # the non-streaming ask is slow too
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, AUDIO)
    assert be.streams == [] and be.synthesized == [] and be.spoken == ["It is sunny."]
    assert E.VOICE_ASSISTANT_INTENT_PROGRESS not in drv._client.kinds()
    assert dict(drv._client.events)[E.VOICE_ASSISTANT_TTS_END] == {"url": URL}
    await drv.async_unload()


async def test_the_phrase_is_made_once_per_voice(env, monkeypatch):
    be = SlowJarvis().install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, AUDIO)
    await run_turn(drv, drv._client, AUDIO)
    assert be.hold_started == 1  # the second run reused it
    assert be.appended.count(HOLD) == 2  # and still said it
    await drv.async_unload()


async def test_a_custom_phrase_is_said(env, monkeypatch):
    monkeypatch.setenv("HOMEHUB_VOICE_HOLD_PHRASE", "Let me think.")
    assert voice_pipeline.VoiceSettings.from_env().hold_phrase == "Let me think."
    be = SlowJarvis().install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, AUDIO)
    assert be.synthesized[0] == "Let me think." and HOLD not in be.synthesized
    await drv.async_unload()


async def test_a_long_wait_is_logged_once(env, monkeypatch, caplog):
    monkeypatch.setattr(voice_pipeline, "HOLD_STILL_WAITING_S", 0.1)
    be = SlowJarvis(delay=0.5).install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    caplog.set_level("INFO", logger="homehub.voice")
    await run_turn(drv, drv._client, AUDIO)
    waits = [r.getMessage() for r in caplog.records if "still waiting for Jarvis" in r.getMessage()]
    assert len(waits) == 1 and f"run {drv.voice.last_run.id}: still waiting for Jarvis after " in waits[0]
    assert be.appended.count(HOLD) == 1  # nothing more is said
    await drv.async_unload()


async def test_a_phrase_that_cannot_be_made_does_not_fail_the_run(env, monkeypatch):
    be = SlowJarvis().install(monkeypatch)
    real = be.synth

    async def synth(text, voice):
        if text == HOLD:
            raise OSError("speech engine busy")
        return await real(text, voice)

    be.synth = synth
    be.install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, AUDIO)
    assert drv.voice.last_run.outcome == "ok" and be.appended[0] == "It will be sunny tomorrow."
    await drv.async_unload()


def test_the_settings_read_the_hold_from_the_environment(monkeypatch):
    monkeypatch.setenv("HOMEHUB_VOICE_HOLD_AFTER_S", "")
    s = voice_pipeline.VoiceSettings.from_env()
    assert s.hold_after_s == 6.0 and s.hold_phrase == HOLD
    assert s.public()["hold_after_s"] == 6.0 and s.public()["hold_phrase"] == HOLD
