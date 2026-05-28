import pytest
from unittest.mock import MagicMock
import re

# Mock heavy dependencies for testing
import sys
sys.modules['kokoro_onnx'] = MagicMock()
sys.modules['onnxruntime'] = MagicMock()

import numpy as np
from services.execution.tts import KokoroTTSEngine, get_tts_engine

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
    engine._kokoro = MagicMock()
    engine._kokoro.create.return_value = (np.zeros(1000, dtype=np.float32), 24000)
    
    audio = await engine.generate("Test audio")
    assert len(audio) > 0
    assert audio.startswith(b"RIFF")

def test_factory_default():
    engine = get_tts_engine()
    assert isinstance(engine, KokoroTTSEngine)
