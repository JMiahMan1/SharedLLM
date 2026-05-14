import pytest
from unittest.mock import MagicMock, patch
import sys
import re

# Mock heavy dependencies for testing
sys.modules['kokoro_onnx'] = MagicMock()
sys.modules['onnxruntime'] = MagicMock()

import numpy as np
from tts import KokoroTTSEngine, get_tts_engine

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

@pytest.mark.asyncio
async def test_kokoro_engine_generate_mock():
    # Mock kokoro_onnx to avoid loading heavy model in test
    with patch("kokoro_onnx.Kokoro") as mock_kokoro:
        mock_instance = mock_kokoro.return_value
        mock_instance.create_stream.return_value = [
            (np.zeros(1000, dtype=np.float32), 24000)
        ]
        
        engine = KokoroTTSEngine()
        # Mocking the initialization
        engine.kokoro = mock_instance
        engine._initialized = True
        
        # Mock os.path.exists to pass the check
        with patch("os.path.exists", return_value=True):
            audio = await engine.generate("Test audio")
            assert len(audio) > 0
            assert audio.startswith(b"RIFF") # WAV header

def test_factory_default():
    engine = get_tts_engine()
    assert isinstance(engine, KokoroTTSEngine)
