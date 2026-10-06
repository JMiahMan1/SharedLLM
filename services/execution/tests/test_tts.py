import sys
from unittest.mock import MagicMock

import pytest

# Mock soundfile BEFORE importing from tts
mock_sf = MagicMock()
sys.modules["soundfile"] = mock_sf

def mock_write(file, data, samplerate, **kwargs):
    file.write(b"fake_audio_data")

mock_sf.write.side_effect = mock_write

from services.config import DEFAULT_TTS_VOICE
from services.execution.tts import (
    PAUSE_STRUCTURE,
    _PAUSE_MARK,
    KokoroTTSEngine,
    _translate_ssmd,
)


def _mock_result(n: int = 1000):
    import numpy as np

    result = MagicMock()
    result.audio = np.zeros(n, dtype=np.float32)
    result.sample_rate = 24000
    return result


@pytest.mark.asyncio
async def test_kokoro_engine_generate_non_blocking(mocker):
    # Mock the pykokoro synthesizer object
    mock_synth = MagicMock()
    mock_synth.synthesize_text.return_value = _mock_result()

    engine = KokoroTTSEngine()
    engine._synth = mock_synth

    audio_bytes = await engine.generate("Hello world is a testing sentence.")

    assert len(audio_bytes) > 0
    mock_synth.synthesize_text.assert_called_once()

    # Verify it was called with the normalized text and the resolved voice
    args, kwargs = mock_synth.synthesize_text.call_args
    assert args[0] == "Hello world is a testing sentence."
    assert kwargs["voice"] == (DEFAULT_TTS_VOICE or "af_heart")
    assert kwargs["language"] == "en"

@pytest.mark.asyncio
async def test_storybook_mode_switches_voices(mocker):
    mock_synth = MagicMock()
    mock_synth.synthesize_text.return_value = _mock_result(500)

    engine = KokoroTTSEngine()
    engine._synth = mock_synth

    text = 'She said "Hello" and he said "Hi"'
    await engine.generate(text, storybook=True)

    assert mock_synth.synthesize_text.call_count >= 2


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("John 3:16 says", "John chapter three, verse sixteen says"),
        ("John 3:16-17 says", "John chapter three, verses sixteen through seventeen says"),
        ("Genesis 1:1 says", "Genesis chapter one, verse one says"),
        ("2 Timothy 3:16-17 says", "Second Timothy chapter three, verses sixteen through seventeen says"),
        ("1 Corinthians 10:13", "First Corinthians chapter ten, verse thirteen"),
        ("1 John 4:8", "First John chapter four, verse eight"),
        ("2 Chronicles 7:13-14 says", "Second Chronicles chapter seven, verses thirteen through fourteen says"),
        ("Philippians 2:9-11", "Philippians chapter two, verses nine through eleven"),
        ("Revelation 1:8", "Revelation chapter one, verse eight"),
        ("Luke 11:1b", "Luke chapter eleven, verse one"),
        ("Psalm 139", "Psalm chapter one hundred thirty-nine"),
    ],
)
def test_normalize_expands_scripture_refs(raw, expected):
    engine = KokoroTTSEngine.__new__(KokoroTTSEngine)
    assert engine._normalize_text(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("from about 1400 BC to about 100 AD.",
         "from about fourteen hundred B.C. to about one hundred A.D."),
        ("The New American Standard Bible 1995 translation.",
         "The New American Standard Bible nineteen ninety-five translation."),
        ("There are 66 books in the Bible.",
         "There are sixty-six books in the Bible."),
    ],
)
def test_normalize_expands_years_and_numbers(raw, expected):
    engine = KokoroTTSEngine.__new__(KokoroTTSEngine)
    assert engine._normalize_text(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "Week 2: Scripture",
        "Day 0: Introduction",
        "Chapter One.",
        "  1. Honor God's Name",
        "  2. Seek God's Kingdom",
        "1) God exists,",
        "2) the God of the Bible is who the Bible claims He is, and",
        "(Luke 11:1b)",
        "They used Matthew 6:9-13 as a template.",
    ],
)
def test_structure_pauses_mark_titles_lists_and_refs(raw):
    engine = KokoroTTSEngine.__new__(KokoroTTSEngine)
    marked = engine._mark_structure_pauses(raw)
    assert _PAUSE_MARK in marked, f"expected a pause marker after: {raw!r}"


def test_structure_pauses_do_not_mark_body_sentences():
    engine = KokoroTTSEngine.__new__(KokoroTTSEngine)
    body = "Scripture is a term used to primarily reference the Bible. There are different versions."
    assert _PAUSE_MARK not in engine._mark_structure_pauses(body)


# ─── SSMD, translated rather than rendered ────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        # A break token becomes the same marker our structure beats use, so it
        # is paid out as silence instead of being read as "...500ms".
        ("It is ...500ms time to breathe.", f"It is {_PAUSE_MARK} time to breathe."),
        ("Welcome! ...p This is a long pause.", f"Welcome! {_PAUSE_MARK} This is a long pause."),
        ("...w weak ...n none.", f"{_PAUSE_MARK} weak {_PAUSE_MARK} none."),
    ],
)
def test_translate_ssmd_turns_breaks_into_our_own_pause_marker(raw, expected):
    assert _translate_ssmd(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        # A wrapper keeps its content and loses its markup: the new API would
        # otherwise spell the markup out.
        ("[123]{as='cardinal'} apples", "123 apples"),
        ('<div voice="af_sarah">Hello there.</div>', "Hello there."),
        ('<div voice="am_michael">There are 66 books.</div>', "There are 66 books."),
    ],
)
def test_translate_ssmd_unwraps_markup_the_new_api_cannot_render(raw, expected):
    assert _translate_ssmd(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Just a plain sentence about the Bible.",
        "There are 66 books in the Bible.",
        "This closes with a real ellipsis ...",
    ],
)
def test_translate_ssmd_leaves_ordinary_text_alone(text):
    assert _translate_ssmd(text) == text


def test_normalize_keeps_the_pause_marker_and_still_expands_reading():
    engine = KokoroTTSEngine.__new__(KokoroTTSEngine)
    normalized = engine._normalize_text(f"John 3:16 says{_PAUSE_MARK}Chapter 5 begins.")
    assert "John chapter three, verse sixteen" in normalized
    assert "Chapter five" in normalized
    assert _PAUSE_MARK in normalized


@pytest.mark.asyncio
async def test_synthesis_pays_a_structure_marker_out_as_real_silence():
    import numpy as np

    mock_synth = MagicMock()
    mock_synth.synthesize_text.return_value = _mock_result()

    engine = KokoroTTSEngine()
    engine._synth = mock_synth

    out, sr = await engine._synthesize(
        f"Chapter One.{_PAUSE_MARK}In the beginning was the Word.", "am_michael"
    )
    # The marker is not speech and pykokoro 0.10 has no markup for one, so the
    # text is synthesized as two pieces with real silence spliced between them.
    assert sr == 24000
    assert [
        call.args[0] for call in mock_synth.synthesize_text.call_args_list
    ] == ["Chapter One.", "In the beginning was the Word."]
    for call in mock_synth.synthesize_text.call_args_list:
        assert _PAUSE_MARK not in call.args[0]
        assert call.kwargs["voice"] == "am_michael"
        assert call.kwargs["language"] == "en"

    gap = int(PAUSE_STRUCTURE * 24000)
    assert len(out) == 1000 + gap + 1000
    assert np.all(out[1000 : 1000 + gap] == 0.0)


@pytest.mark.asyncio
async def test_a_trailing_marker_does_not_leave_a_gap_at_the_end():
    mock_synth = MagicMock()
    mock_synth.synthesize_text.return_value = _mock_result(400)

    engine = KokoroTTSEngine()
    engine._synth = mock_synth

    out, _ = await engine._synthesize(f"Chapter One.{_PAUSE_MARK}", "af_heart")
    assert mock_synth.synthesize_text.call_count == 1
    assert len(out) == 400


# ─── audiobook/regenerate endpoint ────────────────────────────────────────────

def _import_audiobook_app():
    import os
    os.environ.setdefault("INTERNAL_SECRET", "test-secret")
    os.environ.setdefault("EXECUTION_EXTERNAL_HOST", "localhost")
    os.environ.setdefault("DEVICE_REGISTRY_PATH", ":memory:")
    from services.config import INTERNAL_SECRET
    from services.execution.main import app
    return app, INTERNAL_SECRET


def test_audiobook_regenerate_requires_text_files(mocker):
    from fastapi.testclient import TestClient

    app, secret = _import_audiobook_app()
    client = TestClient(app)
    resp = client.post(
        "/execute/audiobook/regenerate",
        headers={"X-Internal-Secret": secret},
        json={"user_context": {"user": "testuser", "is_admin": True}, "text_files": []},
    )
    # Pydantic min_length=1 rejects an empty list before the handler runs.
    assert resp.status_code == 422


def test_audiobook_regenerate_full_pipeline(mocker, tmp_path):
    import subprocess as _sp

    from fastapi.testclient import TestClient

    day1 = tmp_path / "scripture_day_01.txt"
    day2 = tmp_path / "scripture_day_02.txt"
    day1.write_text("Week 2: Scripture\n\nIn the beginning was the Word.")
    day2.write_text("Day 1: Introduction\n\nHonor God's Name.")

    app, secret = _import_audiobook_app()
    client = TestClient(app)

    # Workspace resolution returns the tmp_path as the resolved root.
    mocker.patch(
        "services.execution.handlers.workspace._resolve_workspace_info",
        return_value=(str(tmp_path), {}),
    )
    # TTS synthesizes fake-but-valid relative WAV bytes for every chapter.
    async def fake_tts(text, voice=None, storybook=False):
        wav = bytes("RIFF" + text[:8], "utf-8")
        return wav

    mocker.patch("services.execution.main._text_to_speech", side_effect=fake_tts)

    # Fake ffmpeg via subprocess.run: touch the output MP3 instead of encoding.
    def fake_run(cmd, **kwargs):
        # cmd ends with the output MP3 path (last argv entry).
        mp3_target = cmd[-1]
        with open(mp3_target, "wb") as f:
            f.write(b"ID3fake")
        return _sp.CompletedProcess(cmd, 0, "", "")

    # The endpoint runs the ffmpeg subprocess through asyncio.to_thread; run it
    # synchronously in the test and stub out subprocess.run itself.
    mocker.patch("asyncio.to_thread", side_effect=lambda fn, *a, **k: fn(*a, **k))
    mocker.patch("subprocess.run", side_effect=fake_run)

    resp = client.post(
        "/execute/audiobook/regenerate",
        headers={"X-Internal-Secret": secret},
        json={
            "user_context": {"user": "testuser", "is_admin": True},
            "workspace_id": "ws-abc",
            "text_files": ["scripture_day_01.txt", "scripture_day_02.txt"],
            "output_mp3": "audiobook_scripture.mp3",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert (tmp_path / "scripture_day_01.wav").exists()
    assert (tmp_path / "scripture_day_02.wav").exists()
    assert (tmp_path / "audiobook_scripture.mp3").exists()
    assert body["detail"]["mp3"] == "audiobook_scripture.mp3"
    assert len(body["detail"]["wavs"]) == 2
    assert all(w.get("status") == "SUCCESS" for w in body["detail"]["wavs"])


def test_audiobook_regenerate_missing_file_reports_failure(mocker, tmp_path):
    from fastapi.testclient import TestClient

    app, secret = _import_audiobook_app()
    client = TestClient(app)
    mocker.patch(
        "services.execution.handlers.workspace._resolve_workspace_info",
        return_value=(str(tmp_path), {}),
    )
    resp = client.post(
        "/execute/audiobook/regenerate",
        headers={"X-Internal-Secret": secret},
        json={
            "user_context": {"user": "testuser", "is_admin": True},
            "workspace_id": "ws-abc",
            "text_files": ["missing_day_01.txt"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "FAILURE"
    assert "no audio was synthesized" in body["message"]


def test_audiobook_regenerate_accepts_pdf_source(mocker, tmp_path):
    """A PDF source chapter is text-extracted before synthesis."""
    import subprocess as _sp

    from fastapi.testclient import TestClient

    # A fake PDF in the workspace.
    pdf = tmp_path / "Week 2 - Scripture.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    app, secret = _import_audiobook_app()
    client = TestClient(app)

    mocker.patch(
        "services.execution.handlers.workspace._resolve_workspace_info",
        return_value=(str(tmp_path), {}),
    )
    mocker.patch("subprocess.run", side_effect=lambda cmd, **kw: _sp.CompletedProcess(cmd, 0, "", ""))

    async def fake_extract(path):
        return "Week 2: Scripture\n\nJohn 3:16 says..."

    mock_extract = mocker.patch(
        "services.execution.document_text.extract_document_text",
        side_effect=fake_extract,
    )

    async def fake_tts(text, voice=None, storybook=False):
        return bytes("RIFF" + text[:8], "utf-8")

    mocker.patch("services.execution.main._text_to_speech", side_effect=fake_tts)

    def fake_run(cmd, **kwargs):
        mp3_target = cmd[-1]
        with open(mp3_target, "wb") as f:
            f.write(b"ID3fake")
        return _sp.CompletedProcess(cmd, 0, "", "")

    mocker.patch("asyncio.to_thread", side_effect=lambda fn, *a, **k: fn(*a, **k))
    mocker.patch("subprocess.run", side_effect=fake_run)

    resp = client.post(
        "/execute/audiobook/regenerate",
        headers={"X-Internal-Secret": secret},
        json={
            "user_context": {"user": "testuser", "is_admin": True},
            "workspace_id": "ws-abc",
            "text_files": ["Week 2 - Scripture.pdf"],
            "output_mp3": "audiobook_scripture.mp3",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert (tmp_path / "Week 2 - Scripture.wav").exists()
    assert (tmp_path / "audiobook_scripture.mp3").exists()
    # The extracted text must reach the TTS engine.
    mock_extract.assert_called_once()


# ─── the three files the engine needs, and the route that fetches them ───


def test_the_file_list_carries_the_vocabulary_the_new_api_requires():
    """pykokoro 0.10 refuses to load v1.0 without the phoneme vocabulary.

    The startup fetch and the download route each kept their own list, and both
    omitted it, so a download that reported success produced an engine that
    could not load. One list now, and this pins that it has all three.
    """
    from services.execution.main import _kokoro_model_files

    files = dict(_kokoro_model_files())
    assert set(files) == {"kokoro-v1.0.onnx", "voices-v1.0.bin", "vocab-v1.0.json"}
    assert files["vocab-v1.0.json"].endswith("vocab-v1.0.json")


def test_a_missing_vocabulary_is_named_rather_than_guessed(tmp_path):
    """The refusal has to say which file is missing, and what it is for."""
    (tmp_path / "kokoro-v1.0.onnx").write_bytes(b"model")
    (tmp_path / "voices-v1.0.bin").write_bytes(b"voices")

    engine = KokoroTTSEngine(
        model_path=str(tmp_path / "kokoro-v1.0.onnx"),
        voices_path=str(tmp_path / "voices-v1.0.bin"),
        config_path=str(tmp_path / "vocab-v1.0.json"),
    )
    with pytest.raises(FileNotFoundError) as excinfo:
        engine._ensure_loaded()

    message = str(excinfo.value)
    assert "vocab-v1.0.json" in message
    assert "vocabulary" in message


def test_downloading_skips_files_that_are_already_there(tmp_path, mocker):
    from services.execution.main import _download_kokoro_files, _kokoro_model_files

    for name, _ in _kokoro_model_files():
        (tmp_path / name).write_bytes(b"present")
    mocker.patch("services.config.MODELS_DIR", str(tmp_path))
    run = mocker.patch("subprocess.run")

    results, failures = _download_kokoro_files()

    assert failures == []
    assert run.call_count == 0
    assert all("already exists" in line for line in results)


def test_a_failed_download_is_reported_and_leaves_no_partial(tmp_path, mocker):
    """curl leaves a truncated file behind when it fails.

    Left in place, the next run would see a file and skip the download, so the
    engine would keep failing to load a model that is only half there.
    """
    import subprocess as _sp

    from services.execution.main import _download_kokoro_files

    mocker.patch("services.config.MODELS_DIR", str(tmp_path))

    def fail(cmd, **kwargs):
        with open(cmd[cmd.index("-o") + 1], "wb") as handle:
            handle.write(b"truncated")
        raise _sp.CalledProcessError(22, cmd)

    mocker.patch("subprocess.run", side_effect=fail)

    results, failures = _download_kokoro_files()

    assert len(failures) == 3
    assert any("kokoro-v1.0.onnx" in line for line in failures)
    assert results == failures
    assert list(tmp_path.iterdir()) == []


def test_a_failed_download_is_a_failure_not_a_success(mocker):
    from fastapi.testclient import TestClient

    app, secret = _import_audiobook_app()
    client = TestClient(app)
    failure = "Failed to download vocab-v1.0.json: curl exited 22"
    mocker.patch(
        "services.execution.main._download_kokoro_files",
        return_value=([failure], [failure]),
    )

    resp = client.post("/execute/tts/download", headers={"X-Internal-Secret": secret})

    assert resp.status_code == 502
    body = resp.json()
    assert body["status"] == "FAILURE"
    assert "vocab-v1.0.json" in body["message"]


def test_a_clean_download_reports_what_it_fetched(mocker):
    from fastapi.testclient import TestClient

    app, secret = _import_audiobook_app()
    client = TestClient(app)
    mocker.patch(
        "services.execution.main._download_kokoro_files",
        return_value=(["Successfully downloaded kokoro-v1.0.onnx"], []),
    )

    resp = client.post("/execute/tts/download", headers={"X-Internal-Secret": secret})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert body["results"] == ["Successfully downloaded kokoro-v1.0.onnx"]


# ─── one engine per process ───


def test_the_same_engine_answers_every_call(mocker):
    """A second call must not pay for a second ONNX session.

    Measured on the deployment host, a fresh engine spends 3-4s before it
    makes a sound because its first synthesis loads a 325 MB model into a
    new session. The reader narrates a passage at a time, so an engine per
    call would charge that load to every passage -- and would throw away the
    synthesizer's own in-memory cache with it.
    """
    import services.execution.tts as tts

    mocker.patch.object(tts, "_ENGINE", None)
    built = mocker.patch.object(tts, "KokoroTTSEngine")

    first = tts.get_tts_engine()
    second = tts.get_tts_engine()

    assert first is second
    assert built.call_count == 1


def test_synthesis_goes_through_the_shared_engine(mocker):
    import asyncio

    import services.execution.tts as tts

    engine = MagicMock()
    engine.generate = mocker.AsyncMock(return_value=b"wav")
    mocker.patch.object(tts, "_ENGINE", engine)

    out = asyncio.run(tts.text_to_speech("The Lord is my shepherd.", voice="af_bella"))

    assert out == b"wav"
    engine.generate.assert_awaited_once_with("The Lord is my shepherd.", "af_bella", storybook=False)
    assert tts.get_tts_engine() is engine
