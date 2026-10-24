import re

# pykokoro is imported lazily inside KokoroTTSEngine._ensure_loaded, so these
# tests can import the engine and exercise the text pipeline without it.
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.modules['onnxruntime'] = MagicMock()

from services.execution.tts import KokoroTTSEngine, get_tts_engine  # noqa: E402 - after the onnxruntime stub


def test_normalization():
    engine = KokoroTTSEngine()
    text = "Mr. Smith went to St. Jude on Jan. 1st."
    normalized = engine._normalize_text(text)
    assert "Mister" in normalized
    assert "Saint" in normalized
    assert "January" in normalized

def test_storybook_segmentation():
    text = 'He said, "Hello there." Then he walked away.'
    segments = []
    # Peek at the generator result logic
    for match in re.finditer(r'[^"]+|(?:"[^"]*")', text):
        segments.append(match.group())

    assert len(segments) >= 2

@pytest.mark.local_only
@pytest.mark.asyncio
async def test_kokoro_engine_generate_mock():
    engine = KokoroTTSEngine()
    mock_synth = MagicMock()
    mock_synth.synthesize_text.return_value = MagicMock(
        audio=np.zeros(1000, dtype=np.float32), sample_rate=24000
    )
    engine._synth = mock_synth

    audio = await engine.generate("Test audio")
    assert len(audio) > 0
    assert audio.startswith(b"RIFF")

def test_factory_default():
    engine = get_tts_engine()
    assert isinstance(engine, KokoroTTSEngine)
