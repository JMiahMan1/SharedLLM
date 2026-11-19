"""The voice pipeline's harder listening and its streamed replies, on the stand-in satellite of test_voice_pipeline."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from services.homehub import tts, voice_pipeline
from services.homehub.core import HubError
from services.homehub.tests.conftest import make_hub
from services.homehub.tests.test_voice_pipeline import (  # fixtures and stand-ins, not tests
    DATA,
    MAC,
    URL,
    VAD_SELECT,
    Backends,
    E,
    F,
    FakeSTT,
    VoiceClient,
    _silence,
    _tone,
    _wav,
    env,
    loudness_alone,
    make_driver,
    run_turn,
    settings_from_the_test,
)
from services.homehub.voice_pipeline import EnergyVAD, PipelineError, Reply, read_speech_stream

__all__ = ["env", "loudness_alone", "settings_from_the_test"]  # pytest finds the imported fixtures by name

SPEECH_SELECT = "select.test_device_speech_detection"  # named after the node's device
#: Taken before the autouse fixture swaps it for loudness alone.
REAL_CLASSIFIER = voice_pipeline.speech_classifier


# -- listening ----------------------------------------------------------------------------
def test_a_short_sound_does_not_start_a_command():
    """A chime or a knock (under 300 ms) used to start a command after 90 ms; a voice is longer."""
    vad = EnergyVAD(0.5)
    assert vad.process(_silence(0.3) + _tone(0.2) + _silence(0.6)) == []
    assert vad.speech_frames == 0
    assert vad.process(_tone(0.35)) == ["start"]


def test_a_dip_between_syllables_does_not_reset_the_start():
    vad = EnergyVAD(0.5)
    assert vad.process(_tone(0.15) + _silence(0.06) + _tone(0.2)) == ["start"]


def test_a_sound_the_detector_calls_not_speech_never_starts_a_command():
    """WebRTC's detector filters the start; loudness alone ends it, so a doubted voice is not cut off."""
    verdicts = iter([False] * 30 + [True] * 1000)
    vad = EnergyVAD(0.3, classifier=lambda _frame: next(verdicts))
    assert vad.process(_tone(0.9) + _silence(1.0)) == []  # loud, but "not speech" (a tone, a chime)
    assert vad.rejected_frames == 30 and "did not sound like speech" in vad.heard()
    assert vad.process(_tone(0.4)) == ["start"]           # now it says speech
    assert vad.engine == "webrtc+energy"


def test_a_failing_detector_falls_back_to_loudness():
    def broken(_frame):
        raise RuntimeError("bad frame length")

    vad = EnergyVAD(0.3, classifier=broken)
    assert vad.process(_tone(0.4)) == ["start"]
    assert vad.classifier is None and vad.engine == "energy"


def test_the_follow_up_factor_raises_the_start_only():
    vad = EnergyVAD(0.3, min_rms=250, start_factor=1.5)
    assert vad.start_threshold() == 1.5 * vad.threshold()
    quiet_voice = 300  # over the plain gate (250), under the raised one (375)
    assert vad.process(_silence(1.5) + _tone(0.5, amplitude=int(quiet_voice * 1.414))) == []
    assert vad.process(_tone(0.5, amplitude=8000)) == ["start"]
    # Once started, the end is judged by the plain gate: the quiet voice keeps the command open.
    assert vad.process(_tone(0.5, amplitude=int(quiet_voice * 1.414))) == []


def test_the_webrtc_detector_is_used_when_installed_and_is_optional(monkeypatch):
    import sys

    calls = []

    class FakeVad:
        def __init__(self, mode):
            calls.append(("mode", mode))

        def is_speech(self, frame, rate):
            calls.append(("frame", len(frame), rate))
            return True

    assert REAL_CLASSIFIER("energy", 2) is None  # HOMEHUB_VOICE_VAD=energy: loudness alone
    monkeypatch.setitem(sys.modules, "webrtcvad", SimpleNamespace(Vad=FakeVad))
    classify = REAL_CLASSIFIER("auto", 3)
    assert classify is not None and classify(b"\x00" * 960) is True
    assert calls == [("mode", 3), ("frame", 960, 16000)]  # 30 ms frames of 16 kHz audio, as WebRTC takes them
    monkeypatch.setitem(sys.modules, "webrtcvad", None)  # not installed
    assert REAL_CLASSIFIER("auto", 2) is None


async def test_strict_speech_detection_needs_more_sound(env, monkeypatch):
    monkeypatch.setenv("HOMEHUB_VOICE_NO_SPEECH_S", "0.6")
    Backends(stt=FakeSTT(text="")).install(monkeypatch)
    hub, drv = make_driver()
    await drv.async_setup()
    await hub.call_service("select", "select_option", {"entity_id": SPEECH_SELECT, "option": "strict"})
    assert hub.states.get(SPEECH_SELECT).state == "strict"
    assert drv.voice.speech_detection().min_speech_s == 0.45
    await run_turn(drv, drv._client, [_tone(0.4), _silence(0.3)])
    assert drv.voice.last_run.speech_ms == 0  # 0.4 s is not enough for "strict"
    with pytest.raises(HubError, match="not an option"):
        await hub.call_service("select", "select_option", {"entity_id": SPEECH_SELECT, "option": "loud"})
    with pytest.raises(HubError, match="not an option"):
        await hub.call_service("select", "select_option", {"entity_id": VAD_SELECT, "option": "fast"})
    await drv.async_unload()


async def test_a_follow_up_does_not_send_the_satellites_own_audio_to_speech_to_text(env, monkeypatch):
    be = Backends(reply="Which room?").install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, [_tone(0.6), _silence(1.0)])
    assert not drv.voice.last_run.follow_up
    first = len(be.stt.audio)
    # The follow-up: half a second of the reply's tail or the chime, then the answer.
    await run_turn(drv, drv._client, [_tone(0.5, amplitude=2000), _tone(0.6), _silence(1.0)])
    run = drv.voice.last_run
    assert run.follow_up and run.public()["follow_up"] is True
    sent = len(be.stt.audio) - first
    # The answer and the silence that ended it; the half second before it (16000 bytes) was never sent.
    assert len(_tone(0.6)) < sent <= len(_tone(0.6)) + len(_silence(1.0))
    await drv.async_unload()


async def test_a_new_conversation_is_not_a_follow_up(env, monkeypatch):
    Backends(reply="Which room?").install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    client = drv._client
    await run_turn(drv, client, [_tone(0.6), _silence(1.0)])
    await client.handlers["start"]("another-conversation", 0, SimpleNamespace(volume_multiplier=1.0), "Hey Jarvis")
    task = drv.voice._task
    for i in range(0, len(_tone(0.4)), 960):
        await client.handlers["audio"](_tone(0.4)[i:i + 960], None)
    await client.handlers["stop"](False)
    await asyncio.wait_for(task, 5)
    assert not drv.voice.last_run.follow_up and drv.voice.last_run.speech_ms
    await drv.async_unload()


# -- a satellite adopted on the hub gets its entities ------------------------------------------------
async def test_an_adopted_satellite_gets_its_announce_target_and_settings(env, monkeypatch):
    Backends().install(monkeypatch)
    hub, entry = make_hub("esphome", dict(DATA), [])
    from services.homehub.drivers.esphome import ESPHomeDriver

    drv = ESPHomeDriver(hub, entry)
    drv._make_client = lambda host: VoiceClient(int(F.VOICE_ASSISTANT | F.API_AUDIO | F.ANNOUNCE))  # type: ignore[method-assign]
    hub.drivers[entry.entry_id] = drv
    await drv.async_setup()
    mine = {e.unique_id: e for e in hub.entities.values() if e.config_entry_id == entry.entry_id}
    assert {f"{MAC}-assist_satellite", f"{MAC}-wake_word", f"{MAC}-vad_sensitivity",
            f"{MAC}-speech_detection"} <= set(mine)
    sat = mine[f"{MAC}-assist_satellite"]
    # On the node's device, so it is in the node's room: execution finds announce targets by area.
    assert sat.device_id == "dev1" and hub.entity_area_id(sat.entity_id) == "office"
    assert hub.states.get(sat.entity_id).attributes["supported_features"] == 1  # announce
    assert "announce" in drv.entity_abilities(sat)
    assert mine[f"{MAC}-vad_sensitivity"].entity_category == "config"
    assert hub.states.get(mine[f"{MAC}-speech_detection"].entity_id).attributes["options"] == [
        "sensitive", "default", "strict"]
    # The run's context names it, so a timer set by voice rings there.
    be = Backends().install(monkeypatch)
    await run_turn(drv, drv._client, [_tone(0.6), _silence(1.0)])
    assert be.asked[0][1]["satellite"] == sat.entity_id
    # A reconnect registers nothing twice.
    count = len(hub.entities)
    await drv._client.on_stop(False)
    await asyncio.sleep(0.05)
    assert len(hub.entities) == count
    await drv.async_unload()


async def test_an_imported_satellite_keeps_its_own_entities(env, monkeypatch):
    Backends().install(monkeypatch)
    hub, drv = make_driver()
    before = set(hub.entities)
    await drv.async_setup()
    added = set(hub.entities) - before
    assert added == {SPEECH_SELECT}  # only what HA never had
    await drv.async_unload()


# -- streamed replies ---------------------------------------------------------------------------------
class StreamBackends(Backends):
    """Jarvis streaming its reply a sentence at a time, and the hub's real growing stream."""

    def __init__(self, sentences=("It will be sunny tomorrow.",), rest="Highs near seventy.", whole=None,
                 synth_error=None, **kw):
        super().__init__(**kw)
        self.sentences, self.rest, self.whole, self.synth_error = list(sentences), rest, whole, synth_error
        self.synthesized: list[str] = []
        self.streams: list[tuple[str, tts.ClipStream]] = []
        self.progress_before_answer = None

    def install(self, monkeypatch):
        monkeypatch.setattr(voice_pipeline, "BACKENDS", voice_pipeline.Backends(
            open_stt=lambda _s: self.stt, ask=self.ask, speak=self.speak, clip_audio=lambda _u: _wav(24000, 0.1),
            ask_stream=self.ask_stream, synth=self.synth, open_stream=self.open_stream))
        return self

    def open_stream(self):
        url, stream = tts.open_stream()
        self.streams.append((url, stream))
        return url, stream

    async def ask_stream(self, settings, text, context, on_sentence):
        self.asked.append((text, context))
        for s in self.sentences:
            await on_sentence(s, "af_heart")
            await asyncio.sleep(0.05)  # the model writing the next sentence
        client = VoiceClient.instances[-1]
        self.progress_before_answer = E.VOICE_ASSISTANT_INTENT_PROGRESS in client.kinds()
        whole = self.whole or " ".join([*self.sentences, self.rest]).strip()
        out = Reply(whole)
        out.voice, out.streamed = "af_heart", len(self.sentences)
        out.rest = self.rest if self.sentences else whole
        return out

    async def synth(self, text, voice):
        if self.synth_error:
            raise self.synth_error
        self.synthesized.append(text)
        self.voices.append(voice)
        return _wav(24000, 0.2)


async def test_the_reply_starts_playing_before_the_answer_is_finished(env, monkeypatch):
    be = StreamBackends().install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    client = drv._client
    await run_turn(drv, client, [_silence(0.3), _tone(1.0), _silence(1.0)])
    url, stream = be.streams[0]
    kinds = client.kinds()
    assert kinds == [E.VOICE_ASSISTANT_RUN_START, E.VOICE_ASSISTANT_STT_START, E.VOICE_ASSISTANT_STT_VAD_START,
                     E.VOICE_ASSISTANT_STT_VAD_END, E.VOICE_ASSISTANT_STT_END, E.VOICE_ASSISTANT_INTENT_START,
                     E.VOICE_ASSISTANT_INTENT_PROGRESS, E.VOICE_ASSISTANT_INTENT_END, E.VOICE_ASSISTANT_TTS_START,
                     E.VOICE_ASSISTANT_TTS_END, E.VOICE_ASSISTANT_RUN_END]
    data = dict(client.events)
    assert data[E.VOICE_ASSISTANT_RUN_START] == {"url": url}  # where the node will play it from
    assert data[E.VOICE_ASSISTANT_INTENT_PROGRESS] == {"tts_start_streaming": "1"}
    assert be.progress_before_answer  # the node was told to play while Jarvis was still answering
    assert data[E.VOICE_ASSISTANT_TTS_START] == {"text": "It will be sunny tomorrow. Highs near seventy."}
    assert data[E.VOICE_ASSISTANT_TTS_END] == {"url": url}
    assert be.synthesized == ["It will be sunny tomorrow.", "Highs near seventy."]
    assert be.voices == ["af_heart", "af_heart"] and be.spoken == []  # never spoken twice
    assert stream.closed and len(stream.parts) == 3  # two sentences and the pause between them
    run = drv.voice.last_run
    assert run.reply_mode == "stream" and run.streamed_sentences == 1 and run.outcome == "ok"
    t = run.timings_ms()
    assert {"stt", "first_audio", "intent", "total"} <= set(t) and t["first_audio"] < t["stt"] + t["intent"]
    await drv.async_unload()


async def test_the_log_line_shows_stt_first_audio_and_total(env, monkeypatch, caplog):
    StreamBackends().install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    caplog.set_level("INFO", logger="homehub.voice")
    await run_turn(drv, drv._client, [_tone(0.6), _silence(1.0)])
    line = next(r.getMessage() for r in caplog.records if " run " in r.getMessage() and "ok" in r.getMessage())
    assert "reply stream (1 sentences early)" in line
    for key in ("stt=", "first_audio=", "total="):
        assert key in line
    await drv.async_unload()


async def test_a_shortcut_answer_is_spoken_whole_as_before(env, monkeypatch):
    """A device command or a fast path streams no sentences: the whole reply, one clip, the old events."""
    be = StreamBackends(sentences=(), rest="", whole="The lamp is on.").install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    client = drv._client
    await run_turn(drv, client, [_tone(0.6), _silence(1.0)])
    assert E.VOICE_ASSISTANT_INTENT_PROGRESS not in client.kinds()
    assert dict(client.events)[E.VOICE_ASSISTANT_TTS_END] == {"url": URL}
    assert be.spoken == ["The lamp is on."] and be.synthesized == []
    _url, stream = be.streams[0]
    assert stream.closed and stream.empty  # the unused stream is closed and its URL answers 404
    run = drv.voice.last_run
    assert run.reply_mode == "whole" and "first_audio" in run.timings_ms()
    await drv.async_unload()


async def test_a_node_with_its_own_speaker_waits_for_the_whole_reply(env, monkeypatch):
    be = StreamBackends().install(monkeypatch)
    _hub, drv = make_driver(flags=int(F.VOICE_ASSISTANT | F.API_AUDIO | F.SPEAKER))
    await drv.async_setup()
    client = drv._client
    await run_turn(drv, client, [_tone(0.6), _silence(1.0)])
    assert dict(client.events)[E.VOICE_ASSISTANT_RUN_START] == {}
    assert be.streams == [] and be.spoken == ["It is sunny."]  # the non-streaming ask, as before
    assert client.audio_out
    await drv.async_unload()


async def test_streaming_can_be_turned_off(env, monkeypatch):
    monkeypatch.setenv("HOMEHUB_VOICE_STREAM", "0")
    be = StreamBackends().install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, [_tone(0.6), _silence(1.0)])
    assert be.streams == [] and be.spoken == ["It is sunny."]
    await drv.async_unload()


async def test_a_sentence_that_cannot_be_spoken_ends_the_run_with_an_error(env, monkeypatch):
    StreamBackends(synth_error=HubError("speech engine down")).install(monkeypatch)
    _hub, drv = make_driver()
    await drv.async_setup()
    await run_turn(drv, drv._client, [_tone(0.6), _silence(1.0)])
    kinds = drv._client.kinds()
    assert kinds[-2:] == [E.VOICE_ASSISTANT_ERROR, E.VOICE_ASSISTANT_RUN_END]
    assert drv._client.events[-2][1]["code"] == "tts-failed"
    await drv.async_unload()


# -- the gateway's speech stream ----------------------------------------------------------------------
async def _lines(*objs):
    for o in objs:
        yield (json.dumps(o) + "\n").encode()


async def test_reading_the_gateways_speech_stream():
    said = []

    async def on_sentence(text, voice):
        said.append((text, voice))

    reply = await read_speech_stream(_lines(
        {"message": {"content": "It will be sunny tomorrow."}, "speech": True, "done": False, "voice": "af_heart"},
        {"message": {"content": ""}, "done": True, "reply": "It will be sunny tomorrow. Highs near seventy.",
         "speech_rest": "Highs near seventy.", "voice": "af_heart"}), on_sentence)
    assert said == [("It will be sunny tomorrow.", "af_heart")]
    assert reply == "It will be sunny tomorrow. Highs near seventy."
    assert reply.rest == "Highs near seventy." and reply.streamed == 1 and reply.voice == "af_heart"


async def test_a_whole_answer_in_the_stream_is_the_reply_to_say():
    """A shortcut answered at once (one chunk, then done) streams nothing: it is said whole."""
    reply = await read_speech_stream(_lines({"message": {"content": "The lamp is on."}, "done": False},
                                            {"done": True}), lambda *_a: None)
    assert reply == "The lamp is on." and reply.streamed == 0 and reply.rest == "The lamp is on."


@pytest.mark.parametrize("chunk", [{"done": True, "error": "model server down"},
                                   {"message": {"content": "[ERROR]: model server down"}, "done": True}])
async def test_a_failed_answer_is_an_intent_error(chunk):
    with pytest.raises(PipelineError) as err:
        await read_speech_stream(_lines(chunk), lambda *_a: None)
    assert err.value.code == "intent-failed" and "model server down" in err.value.message


async def test_an_answer_cut_off_is_an_error():
    with pytest.raises(PipelineError, match="ended before"):
        await read_speech_stream(_lines({"message": {"content": "It will"}, "done": False}), lambda *_a: None)


# -- the growing stream itself ------------------------------------------------------------------------
async def test_the_clip_stream_grows_and_ends(monkeypatch):
    monkeypatch.setenv("HOMEHUB_EXTERNAL_URL", "http://hub.example.com:8123")
    tts.reset()
    url, stream = tts.open_stream()
    assert url.startswith("http://hub.example.com:8123/api/tts_stream/") and url.endswith(".wav")
    assert tts.stream(url.rsplit("/", 1)[1][:-4]) is stream
    got: list[bytes] = []

    async def read():
        async for part in stream.body():
            got.append(part)

    reader = asyncio.create_task(read())
    await asyncio.sleep(0)
    assert got == []  # nothing until the first sentence: its rate goes in the header
    stream.append(_wav(24000, 0.1))
    await asyncio.sleep(0)
    assert got[0][:4] == b"RIFF" and got[0][4:8] == b"\xff\xff\xff\xff"  # the length is not known yet
    stream.append(_wav(16000, 0.1))  # another rate is resampled to the first
    stream.close()
    await asyncio.wait_for(reader, 1)
    assert len(got) == 4 and len(got[3]) == 2 * 2400  # 0.1 s at 24 kHz
    late = [p async for p in stream.body()]
    assert int.from_bytes(late[0][40:44], "little") == sum(len(p) for p in late[1:])  # a late reader: real length


def test_the_stream_url_serves_the_reply_without_a_bearer(tmp_path, monkeypatch):
    from services.homehub.tests.test_tts import _api_client

    monkeypatch.setenv("HOMEHUB_EXTERNAL_URL", "http://hub.example.com:8123")
    client = _api_client(tmp_path)
    tts.reset()
    url, stream = tts.open_stream()
    stream.append(_wav(24000, 0.1))
    stream.close()
    path = url.split("8123", 1)[1]
    r = client.get(path)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert client.head(path).status_code == 200
    assert client.get("/api/tts_stream/nope.wav").status_code == 404
    _url2, empty = tts.open_stream()
    empty.close()
    assert client.get(_url2.split("8123", 1)[1]).status_code == 404
