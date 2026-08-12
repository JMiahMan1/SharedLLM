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
