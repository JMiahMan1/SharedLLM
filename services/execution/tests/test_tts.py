import sys
from unittest.mock import MagicMock

import pytest

# Mock soundfile BEFORE importing from tts
mock_sf = MagicMock()
sys.modules["soundfile"] = mock_sf

def mock_write(file, data, samplerate, **kwargs):
    file.write(b"fake_audio_data")

mock_sf.write.side_effect = mock_write

from services.execution.tts import (
    _PAUSE_MARK,
    PAUSE_STRUCTURE,
    KokoroTTSEngine,
)


@pytest.mark.asyncio
async def test_kokoro_engine_generate_non_blocking(mocker):
    # Mock the ONNX Kokoro object
    mock_kokoro = MagicMock()
    # Create returns (samples, sample_rate)
    import numpy as np
    mock_kokoro.create.return_value = (np.zeros(1000), 24000)

    engine = KokoroTTSEngine()
    engine._kokoro = mock_kokoro

    audio_bytes = await engine.generate("Hello world")

    assert len(audio_bytes) > 0
    mock_kokoro.create.assert_called_once()

    # Verify it was called with the normalized text
    args, kwargs = mock_kokoro.create.call_args
    assert args[0] == "Hello world"
    assert kwargs["voice"] == "af_heart"

@pytest.mark.asyncio
async def test_storybook_mode_switches_voices(mocker):
    mock_kokoro = MagicMock()
    import numpy as np
    mock_kokoro.create.return_value = (np.zeros(500), 24000)

    engine = KokoroTTSEngine()
    engine._kokoro = mock_kokoro

    text = 'She said "Hello" and he said "Hi"'
    await engine.generate(text, storybook=True)

    assert mock_kokoro.create.call_count >= 2


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


@pytest.mark.asyncio
async def test_synthesis_inserts_structure_pause_silence(mocker):
    import numpy as np

    mock_kokoro = MagicMock()
    sample = np.zeros(1000, dtype=np.float32)
    mock_kokoro.create.return_value = (sample, 24000)
    engine = KokoroTTSEngine()
    engine._kokoro = mock_kokoro

    out, sr = await engine._synthesize(
        f"Chapter One.{_PAUSE_MARK}In the beginning was the Word.", "am_michael"
    )
    # Two text segments synthesized plus a PAUSE_STRUCTURE silence in between.
    assert mock_kokoro.create.call_count == 2
    expected_silence = int(PAUSE_STRUCTURE * sr)
    assert len(out) == 2000 + expected_silence
    # The interior silence samples are all zeros.
    assert np.all(out[1000:1000 + expected_silence] == 0)


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
