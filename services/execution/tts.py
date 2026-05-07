# services/execution/tts.py
"""
Modular TTS Engine for SharedLLM.
Allows swapping between cloud-based (edge-tts) and local (kokoro, etc.) providers.
"""
import logging
import asyncio
from uuid import uuid4
from typing import Optional, Protocol

log = logging.getLogger("execution.tts")

class TTSEngine(Protocol):
    async def generate(self, text: str, voice: Optional[str] = None) -> bytes:
        ...

class EdgeTTSEngine:
    """Cloud-based TTS using Microsoft Edge's API."""
    async def generate(self, text: str, voice: Optional[str] = "en-US-GuyNeural") -> bytes:
        import edge_tts
        communicate = edge_tts.Communicate(text, voice)
        audio_bytes = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_bytes += chunk["data"]
        return audio_bytes

class KokoroTTSEngine:
    """Local-first TTS (Placeholder for future implementation)."""
    async def generate(self, text: str, voice: Optional[str] = None) -> bytes:
        # TODO: Implement local kokoro-tts inference here
        log.warning("Kokoro-TTS not yet implemented. Falling back or failing.")
        raise NotImplementedError("Kokoro-TTS support is planned but not yet active.")

# Factory to get the current engine
def get_tts_engine() -> TTSEngine:
    # This can be configured via environment variable in the future
    return EdgeTTSEngine()

async def text_to_speech(text: str, voice: Optional[str] = None) -> bytes:
    """Helper to generate audio bytes from text."""
    engine = get_tts_engine()
    return await engine.generate(text, voice)
