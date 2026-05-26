import logging
import sys
import asyncio
import re
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import DEFAULT_TTS_VOICE, MODELS_DIR
from typing import Optional, Protocol, List, TYPE_CHECKING
import numpy as np

if TYPE_CHECKING:
    import kokoro_onnx  # pyright: ignore[reportMissingImports,reportUnusedImport]
    import soundfile as sf  # pyright: ignore[reportMissingImports,reportUnusedImport]

log = logging.getLogger("execution.tts")

class TTSEngine(Protocol):
    async def generate(self, text: str, voice: Optional[str] = None, storybook: bool = False) -> bytes:
        ...
    def list_voices(self) -> List[str]:
        ...

class KokoroTTSEngine:
    """Local-first TTS using Kokoro-v1.0 (ONNX). Includes Storybook Mode logic."""
    def __init__(self, model_path: str = "", voices_path: str = ""):
        if not model_path:
            model_path = os.path.join(MODELS_DIR, "kokoro-v1.0.onnx")
        if not voices_path:
            voices_path = os.path.join(MODELS_DIR, "voices-v1.0.bin")
        self.model_path = model_path
        self.voices_path = voices_path
        self._kokoro = None
        self._voices = None

    def _ensure_loaded(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro  # pyright: ignore[reportMissingImports]
            if not os.path.exists(self.model_path):
                log.error(f"Kokoro model not found at {self.model_path}")
                raise FileNotFoundError(f"Kokoro model missing: {self.model_path}")
            self._kokoro = Kokoro(self.model_path, self.voices_path)

    def list_voices(self) -> List[str]:
        """Returns a list of available voice styles in the current model."""
        return [
            "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
            "am_adam", "am_michael", "bf_emma", "bf_isabella", "bm_george", "bm_lewis"
        ]

    async def generate(self, text: str, voice: Optional[str] = None, storybook: bool = False) -> bytes:
        self._ensure_loaded()
        
        if not voice:
            voice = DEFAULT_TTS_VOICE or "af_heart"
        
        if storybook:
            return await self._generate_storybook(text, voice)
        
        text = self._normalize_text(text)
        assert self._kokoro is not None
        samples, sample_rate = await asyncio.to_thread(
            self._kokoro.create, text, voice=voice, speed=1.0, lang="en-us"
        )
        return self._samples_to_bytes(samples, sample_rate)


    async def _generate_storybook(self, text: str, primary_voice: str) -> bytes:
        """Splits text into dialogue/narrative and switches voices."""
        # Storybook logic ported from Docket-TTS
        segments = self._segment_text(text)
        all_samples = []
        last_sample_rate = 24000

        for _i, (content, is_dialogue) in enumerate(segments):
            voice = primary_voice
            if is_dialogue:
                # Infer gender from preceding context (last 150 chars)
                context = text[:text.find(content)][-150:]
                gender = self._infer_speaker_gender(context)
                voice = "am_adam" if gender == "male" else "af_bella"
            
            normalized = self._normalize_text(content)
            if not normalized.strip(): continue

            assert self._kokoro is not None
            samples, last_sample_rate = await asyncio.to_thread(
                self._kokoro.create, normalized, voice=voice, speed=1.0, lang="en-us"
            )
            all_samples.append(samples)
            
        if not all_samples: return b""
        combined = np.concatenate(all_samples)
        return self._samples_to_bytes(combined, last_sample_rate)

    def _segment_text(self, text: str) -> List[tuple]:
        """Splits text into (content, is_dialogue) tuples."""
        parts = re.split(r'("[^"]+")', text)
        result = []
        for p in parts:
            if not p.strip(): continue
            is_dialogue = p.startswith('"') and p.endswith('"')
            result.append((p.strip('"'), is_dialogue))
        return result

    def _infer_speaker_gender(self, context: str) -> str:
        """Simple heuristic to pick a voice based on pronouns/verbs."""
        context = context.lower()
        male_hints = ["he said", "he replied", "his voice", "the man", "himself"]
        female_hints = ["she said", "she replied", "her voice", "the woman", "herself"]
        
        m_count = sum(1 for h in male_hints if h in context)
        f_count = sum(1 for h in female_hints if h in context)
        
        return "male" if m_count > f_count else "female"

    def _normalize_text(self, text: str) -> str:
        """Robust normalization for high-quality TTS. Ported/expanded from Docket-TTS."""
        # Common Abbreviations
        replacements = {
            r"\bDr\.\b": "Doctor",
            r"\bMr\.\b": "Mister",
            r"\bMrs\.\b": "Missus",
            r"\bMs\.\b": "Miss",
            r"\bSt\.\b": "Saint",
            r"\bi\.e\.\b": "that is",
            r"\be\.g\.\b": "for example",
            r"\bJan\.\b": "January",
            r"\bFeb\.\b": "February",
            r"\bMar\.\b": "March",
            r"\bApr\.\b": "April",
            r"\bAug\.\b": "August",
            r"\bSep\.\b": "September",
            r"\bOct\.\b": "October",
            r"\bNov\.\b": "November",
            r"\bDec\.\b": "December",
            r"\bvs\.\b": "versus",
            r"\betc\.\b": "et cetera",
            r"\bapprox\.\b": "approximately",
        }
        
        # Roman Numerals (Simple cases for chapters)
        roman_map = {
            r"\bChapter I\b": "Chapter 1",
            r"\bChapter II\b": "Chapter 2",
            r"\bChapter III\b": "Chapter 3",
            r"\bChapter IV\b": "Chapter 4",
            r"\bChapter V\b": "Chapter 5",
            r"\bChapter VI\b": "Chapter 6",
            r"\bChapter VII\b": "Chapter 7",
            r"\bChapter VIII\b": "Chapter 8",
            r"\bChapter IX\b": "Chapter 9",
            r"\bChapter X\b": "Chapter 10",
        }
        replacements.update(roman_map)

        for pattern, replacement in replacements.items():
            # Remove trailing \b because the period already acts as a boundary
            # and \b after a period won't match if followed by a space.
            pattern = pattern.rstrip(r"\b")
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        
        return text

    def _samples_to_bytes(self, samples: np.ndarray, sample_rate: int) -> bytes:
        import io
        import soundfile as sf  # pyright: ignore[reportMissingImports]
        buffer = io.BytesIO()
        sf.write(buffer, samples, sample_rate, format='WAV')
        return buffer.getvalue()

# Factory to get the current engine
def get_tts_engine() -> TTSEngine:
    # Always return Kokoro as Edge is deprecated/unreliable
    return KokoroTTSEngine()



async def text_to_speech(text: str, voice: Optional[str] = None, storybook: bool = False) -> bytes:
    """Helper to generate audio bytes from text."""
    engine = get_tts_engine()
    return await engine.generate(text, voice, storybook=storybook)

