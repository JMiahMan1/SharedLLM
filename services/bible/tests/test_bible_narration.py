"""Read-aloud: the passage becomes audio, or an honest sentence explaining why not.

The speech engine is stubbed at the HTTP boundary (``aiohttp.ClientSession``)
rather than deep inside ``services.execution.tts``, because the contract this
module owns is the conversation with the execution service -- the request shape,
the failure translation and the cache -- not the speech engine itself.
"""

import base64
import json

import pytest

from services.bible import corpus, narration
from services.bible.models import NarrationAudio
from services.bible.refs import parse_reference

pytestmark = pytest.mark.unit

RIFF = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 16


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


class _Response:
    """The little aiohttp response surface this module touches."""

    def __init__(self, status: int, text: str):
        self.status = status
        self._text = text

    async def text(self) -> str:
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Records the POST bodies and replays a queued response."""

    def __init__(self, *responses: _Response):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json or {}))
        if not self._responses:
            raise AssertionError("unexpected second call to the speech engine")
        return self._responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _success(audio: bytes = RIFF, mime: str = "audio/wav") -> _Response:
    return _Response(
        200,
        '{"status": "SUCCESS", "message": "TTS generated successfully", '
        '"service": "tts", "detail": {"audio_base64": "%s", "mime_type": "%s", '
        '"length_bytes": %d}}' % (_b64(audio), mime, len(audio)),
    )


def _failure(message: str) -> _Response:
    body = json.dumps({"status": "FAILURE", "message": message, "service": "tts"})
    return _Response(200, body)


def _patch(monkeypatch, session: _Session | None, *, value_error: Exception | None = None):
    def _factory(*args, **kwargs):
        if value_error is not None:
            raise value_error
        return session

    monkeypatch.setattr(narration.aiohttp, "ClientSession", _factory)


# ── the script ──────────────────────────────────────────────────────────────


def test_script_is_the_reference_then_every_verse():
    script = narration.build_script(
        "John 3:16-17",
        [
            {"text": "For God so loved the world."},
            {"text": "  That whoever believes  "},
            {"text": "For God did not send his Son."},
        ],
    )
    assert script.text == (
        "John 3:16-17\n\nFor God so loved the world.\n\nThat whoever believes\n\nFor God did not send his Son."
    )


def test_script_refuses_a_passage_with_no_text():
    with pytest.raises(narration.NarrationError) as err:
        narration.build_script("John 3:16", [])
    assert "no text in this translation" in str(err.value)


def test_script_refuses_a_passage_of_blank_verses():
    with pytest.raises(narration.NarrationError):
        narration.build_script("John 3:16", [{"text": "   "}, {"text": ""}])


def test_script_refuses_more_verses_than_a_chapter_is_worth_listening_to():
    verses = [{"text": f"verse {n}"} for n in range(narration.MAX_VERSES + 1)]
    with pytest.raises(narration.NarrationError) as err:
        narration.build_script("Psalms 119", verses)
    message = str(err.value)
    assert f"{narration.MAX_VERSES + 1} verses" in message
    assert "Select a shorter passage" in message


def test_script_refuses_a_passage_too_long_to_speak():
    verses = [{"text": "x" * narration.MAX_CHARACTERS} for _ in range(2)]
    with pytest.raises(narration.NarrationError) as err:
        narration.build_script("Psalms 119", verses)
    assert "characters" in str(err.value)
    assert "Select a shorter passage" in str(err.value)


def test_script_refuses_text_too_short_to_be_worth_speaking():
    with pytest.raises(narration.NarrationError) as err:
        narration.build_script("John 3:16", [{"text": "A"}])
    assert "too little text" in str(err.value)


# ── the conversation with the speech engine ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_passage_is_sent_to_the_speech_engine_and_the_audio_comes_back(loaded, monkeypatch):
    engine = _Session(_success())
    _patch(monkeypatch, engine)
    result = await narration.narrate(
        loaded,
        version="kjv",
        spans=parse_reference("John 3"),
        reference="John 3",
        voice=None,
        execution_url="http://execution:8003",
        internal_secret="secret",
    )
    url, body = engine.calls[0]
    assert url == "http://execution:8003/execute/tts"
    assert body["user_context"] == {"user": "jarvis"}
    assert body["text"].startswith("John 3\n\n")
    assert "voice" not in body
    assert result["cached"] is False
    assert base64.b64decode(result["audio_base64"]) == RIFF
    assert result["length_bytes"] == len(RIFF)
    assert result["mime_type"] == "audio/wav"


@pytest.mark.asyncio
async def test_the_chosen_voice_is_sent(loaded, monkeypatch):
    engine = _Session(_success())
    _patch(monkeypatch, engine)
    await narration.narrate(
        loaded,
        version="kjv",
        spans=parse_reference("John 3"),
        reference="John 3",
        voice="am_adam",
        execution_url="http://execution:8003",
        internal_secret="secret",
    )
    assert engine.calls[0][1]["voice"] == "am_adam"


@pytest.mark.asyncio
async def test_the_internal_secret_is_sent(loaded, monkeypatch):
    seen: dict = {}

    class _SecretSession(_Session):
        def post(self, url, json=None, headers=None, timeout=None):
            seen.update(headers or {})
            return super().post(url, json=json, headers=headers, timeout=timeout)

    engine = _SecretSession(_success())
    _patch(monkeypatch, engine)
    await narration.narrate(
        loaded,
        version="kjv",
        spans=parse_reference("John 3"),
        reference="John 3",
        voice=None,
        execution_url="http://execution:8003",
        internal_secret="shhh",
    )
    assert seen["X-Internal-Secret"] == "shhh"


@pytest.mark.asyncio
async def test_an_unconfigured_execution_service_says_so(loaded):
    with pytest.raises(narration.NarrationUnavailable) as err:
        await narration.narrate(
            loaded,
            version="kjv",
            spans=parse_reference("John 3"),
            reference="John 3",
            voice=None,
            execution_url="",
            internal_secret="secret",
        )
    message = str(err.value)
    assert "EXECUTION_SVC_URL" in message
    assert "execution_svc_url" in message


@pytest.mark.asyncio
async def test_a_missing_voice_file_names_the_command_that_installs_it(loaded, monkeypatch):
    _patch(monkeypatch, _Session(_failure("TTS generation failed: Kokoro voices missing: /app/models/voices-v1.0.bin")))
    with pytest.raises(narration.NarrationUnavailable) as err:
        await narration.narrate(
            loaded,
            version="kjv",
            spans=parse_reference("John 3"),
            reference="John 3",
            voice=None,
            execution_url="http://execution:8003",
            internal_secret="secret",
        )
    message = str(err.value)
    assert "Kokoro voices missing" in message
    assert "/execute/tts/download" in message


@pytest.mark.asyncio
async def test_another_failure_is_reported_verbatim(loaded, monkeypatch):
    _patch(monkeypatch, _Session(_failure("TTS generation returned empty bytes")))
    with pytest.raises(narration.NarrationUnavailable) as err:
        await narration.narrate(
            loaded,
            version="kjv",
            spans=parse_reference("John 3"),
            reference="John 3",
            voice=None,
            execution_url="http://execution:8003",
            internal_secret="secret",
        )
    assert str(err.value) == "TTS generation returned empty bytes"


@pytest.mark.asyncio
async def test_a_success_with_no_audio_is_not_treated_as_audio(loaded, monkeypatch):
    _patch(monkeypatch, _Session(_Response(200, '{"status": "SUCCESS", "message": "done", "detail": {}}')))
    with pytest.raises(narration.NarrationUnavailable) as err:
        await narration.narrate(
            loaded,
            version="kjv",
            spans=parse_reference("John 3"),
            reference="John 3",
            voice=None,
            execution_url="http://execution:8003",
            internal_secret="secret",
        )
    assert "sent no audio" in str(err.value)


@pytest.mark.asyncio
async def test_an_undecodable_payload_is_reported(loaded, monkeypatch):
    _patch(monkeypatch, _Session(_Response(200, '{"status": "SUCCESS", "detail": {"audio_base64": "not base64!"}}')))
    with pytest.raises(narration.NarrationUnavailable) as err:
        await narration.narrate(
            loaded,
            version="kjv",
            spans=parse_reference("John 3"),
            reference="John 3",
            voice=None,
            execution_url="http://execution:8003",
            internal_secret="secret",
        )
    assert "could not decode" in str(err.value)


@pytest.mark.asyncio
async def test_something_that_is_not_json_is_reported(loaded, monkeypatch):
    _patch(monkeypatch, _Session(_Response(200, "<html>oops</html>")))
    with pytest.raises(narration.NarrationUnavailable) as err:
        await narration.narrate(
            loaded,
            version="kjv",
            spans=parse_reference("John 3"),
            reference="John 3",
            voice=None,
            execution_url="http://execution:8003",
            internal_secret="secret",
        )
    assert "not JSON" in str(err.value)


@pytest.mark.asyncio
async def test_an_upstream_error_status_is_reported_with_its_detail(loaded, monkeypatch):
    _patch(monkeypatch, _Session(_Response(403, '{"detail": "Forbidden"}')))
    with pytest.raises(narration.NarrationUnavailable) as err:
        await narration.narrate(
            loaded,
            version="kjv",
            spans=parse_reference("John 3"),
            reference="John 3",
            voice=None,
            execution_url="http://execution:8003",
            internal_secret="secret",
        )
    assert str(err.value) == "Forbidden"


@pytest.mark.asyncio
async def test_an_unreachable_engine_is_reported(loaded, monkeypatch):
    _patch(monkeypatch, None, value_error=OSError("Connection refused"))
    with pytest.raises(narration.NarrationUnavailable) as err:
        await narration.narrate(
            loaded,
            version="kjv",
            spans=parse_reference("John 3"),
            reference="John 3",
            voice=None,
            execution_url="http://execution:8003",
            internal_secret="secret",
        )
    assert "could not be reached" in str(err.value)
    assert "Connection refused" in str(err.value)


# ── the cache ───────────────────────────────────────────────────────────────


def test_the_cache_key_separates_translation_reference_and_voice():
    base = narration.cache_key("kjv", "John 3", "am_adam")
    assert base == narration.cache_key("kjv", "John 3", "am_adam")
    assert base != narration.cache_key("nkjv", "John 3", "am_adam")
    assert base != narration.cache_key("kjv", "John 4", "am_adam")
    assert base != narration.cache_key("kjv", "John 3", "af_heart")


@pytest.mark.asyncio
async def test_a_second_play_reuses_the_stored_audio(loaded, monkeypatch):
    engine = _Session(_success())
    _patch(monkeypatch, engine)
    await narration.narrate(
        loaded,
        version="kjv",
        spans=parse_reference("John 3"),
        reference="John 3",
        voice="af_heart",
        execution_url="http://execution:8003",
        internal_secret="secret",
    )
    second = await narration.narrate(
        loaded,
        version="kjv",
        spans=parse_reference("John 3"),
        reference="John 3",
        voice="af_heart",
        execution_url="http://execution:8003",
        internal_secret="secret",
    )
    assert len(engine.calls) == 1, "the second play must not reach the speech engine"
    assert second["cached"] is True
    assert base64.b64decode(second["audio_base64"]) == RIFF


@pytest.mark.asyncio
async def test_a_different_voice_is_its_own_narration(loaded, monkeypatch):
    engine = _Session(_success(), _success(audio=b"RIFF-other"))
    _patch(monkeypatch, engine)
    for voice in ("af_heart", "am_adam"):
        await narration.narrate(
            loaded,
            version="kjv",
            spans=parse_reference("John 3"),
            reference="John 3",
            voice=voice,
            execution_url="http://execution:8003",
            internal_secret="secret",
        )
    assert len(engine.calls) == 2


@pytest.mark.asyncio
async def test_a_cache_hit_survives_a_translation_being_reimported(loaded, corpus_file, monkeypatch):
    """Re-importing the text is not a reason to speak it again.

    ``import_corpus`` rewrites the verse rows and leaves the cache alone, so a
    family that reinstalls a translation does not lose every recording they have.
    """
    engine = _Session(_success())
    _patch(monkeypatch, engine)
    await narration.narrate(
        loaded,
        version="kjv",
        spans=parse_reference("John 3"),
        reference="John 3",
        voice=None,
        execution_url="http://execution:8003",
        internal_secret="secret",
    )
    corpus.import_corpus(loaded, code="kjv", name="King James Version", source_path=corpus_file)
    replay = await narration.narrate(
        loaded,
        version="kjv",
        spans=parse_reference("John 3"),
        reference="John 3",
        voice=None,
        execution_url="http://execution:8003",
        internal_secret="secret",
    )
    assert len(engine.calls) == 1
    assert replay["cached"] is True


def test_clearing_the_cache_forgets_one_translation(loaded):
    row = NarrationAudio(key="k1", version_code="kjv", audio=RIFF)
    other = NarrationAudio(key="k2", version_code="nkjv", audio=RIFF)
    loaded.add(row)
    loaded.add(other)
    loaded.commit()
    assert narration.clear(loaded, "kjv") == 1
    assert loaded.get(NarrationAudio, "k1") is None
    assert loaded.get(NarrationAudio, "k2") is not None
    assert narration.clear(loaded) == 1


@pytest.mark.asyncio
async def test_the_verse_count_travels_with_the_cached_audio(loaded, monkeypatch):
    _patch(monkeypatch, _Session(_success()))
    result = await narration.narrate(
        loaded,
        version="kjv",
        spans=parse_reference("John 3"),
        reference="John 3",
        voice=None,
        execution_url="http://execution:8003",
        internal_secret="secret",
    )
    assert result["verse_count"] > 0
    stored = loaded.get(NarrationAudio, narration.cache_key("kjv", "John 3", "default"))
    assert stored.verse_count == result["verse_count"]