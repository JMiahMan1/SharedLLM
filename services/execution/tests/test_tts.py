import sys
import pytest
from unittest.mock import MagicMock

# Mock soundfile BEFORE importing from tts
mock_sf = MagicMock()
sys.modules["soundfile"] = mock_sf

def mock_write(file, data, samplerate, **kwargs):
    file.write(b"fake_audio_data")

mock_sf.write.side_effect = mock_write

from services.execution.tts import KokoroTTSEngine

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
