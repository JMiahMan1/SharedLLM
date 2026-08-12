import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING, Protocol

import numpy as np

from services.config import DEFAULT_TTS_VOICE, MODELS_DIR

if TYPE_CHECKING:
    pass  # pyright: ignore[reportMissingImports,reportUnusedImport]

log = logging.getLogger("execution.tts")

# Kokoro ONNX caps input at ~510 tokens. Docket-TTS learned to chunk at sentence
# boundaries with inter-chunk pauses; the same approach lives here.
MAX_TTS_CHUNK_CHARS = 1500  # ~375 tokens — safely under the 510-token cap
PAUSE_SENTENCE = 0.5  # seconds of silence after . ? !
PAUSE_SEMI = 0.3      # after ; :
PAUSE_COMMA = 0.2     # between other chunks
SAMPLE_RATE_HZ = 24000

# Bible books (with cardinal prefixes like "1 Corinthians") used to expand
# scripture references into natural narration. Longest-first ordering so
# multi-word books match before their substrings ("Song of Solomon" before
# "Solomon"). Book number prefixes are mapped to ordinals at expansion time.
BIBLE_BOOKS = (
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy",
    "Joshua", "Judges", "Ruth", "1 Samuel", "2 Samuel",
    "1 Kings", "2 Kings", "1 Chronicles", "2 Chronicles", "Ezra",
    "Nehemiah", "Esther", "Job", "Psalm", "Psalms", "Proverbs",
    "Ecclesiastes", "Song of Solomon", "Isaiah", "Jeremiah", "Lamentations",
    "Ezekiel", "Daniel", "Hosea", "Joel", "Amos", "Obadiah", "Jonah",
    "Micah", "Nahum", "Habakkuk", "Zephaniah", "Haggai", "Zechariah", "Malachi",
    "Matthew", "Mark", "Luke", "John", "Acts", "Romans",
    "1 Corinthians", "2 Corinthians", "Galatians", "Ephesians", "Philippians",
    "Colossians", "1 Thessalonians", "2 Thessalonians", "1 Timothy", "2 Timothy",
    "Titus", "Philemon", "Hebrews", "James", "1 Peter", "2 Peter",
    "1 John", "2 John", "3 John", "Jude", "Revelation",
)
_BOOK_ORDINAL = {"1": "First", "2": "Second", "3": "Third"}
_ERA_WORDS = {
    "BC": "B.C.",
    "B.C": "B.C.",
    "B.C.": "B.C.",
    "AD": "A.D.",
    "A.D": "A.D.",
    "A.D.": "A.D.",
    "BCE": "B.C.",
    "CE": "A.D.",
}

_NUM_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_NUM_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _num_to_words(n: int) -> str:
    """Convert an integer (0-9999) into spoken English words."""
    if n < 0 or n > 9999:
        return str(n)
    if n < 20:
        return _NUM_ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _NUM_TENS[t] + (("-" + _NUM_ONES[o]) if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return _NUM_ONES[h] + " hundred" + ((" " + _num_to_words(r)) if r else "")
    th, r = divmod(n, 1000)
    return _num_to_words(th) + " thousand" + ((" " + _num_to_words(r)) if r else "")


def _year_to_words(y: int) -> str:
    """Read a year naturally: 1995 -> 'nineteen ninety-five', 1400 -> 'fourteen hundred'."""
    if y < 100:
        return _num_to_words(y)
    if y < 1000:
        return _num_to_words(y)
    first, last = divmod(y, 100)
    if last == 0:
        return _num_to_words(first) + " hundred"
    if first < 10:
        return _num_to_words(y)
    return _num_to_words(first) + " " + _num_to_words(last)


class TTSEngine(Protocol):
    async def generate(self, text: str, voice: str | None = None, storybook: bool = False) -> bytes:
        ...
    def list_voices(self) -> list[str]:
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

    def list_voices(self) -> list[str]:
        """Returns a list of available voice styles in the current model."""
        return [
            "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
            "am_adam", "am_michael", "bf_emma", "bf_isabella", "bm_george", "bm_lewis"
        ]

    async def generate(self, text: str, voice: str | None = None, storybook: bool = False) -> bytes:
        self._ensure_loaded()

        if not voice:
            voice = DEFAULT_TTS_VOICE or "af_heart"

        if storybook:
            return await self._generate_storybook(text, voice)

        text = self._normalize_text(text)
        samples, sample_rate = await self._synthesize(text, voice)
        if len(samples) == 0:
            return b""
        return self._samples_to_bytes(samples, sample_rate)

    async def _synthesize(self, text: str, voice: str) -> tuple[np.ndarray, int]:
        """Synthesize text in sentence-sized chunks with natural pauses between them.

        Ported from Docket-TTS: the Kokoro ONNX engine caps input at ~510 tokens,
        so long texts MUST be split at sentence boundaries and re-joined. Pauses
        (0.5s after .?!, 0.3s after ;:, 0.2s otherwise) make the result sound like
        a human narrator instead of a run-on stream.
        """
        assert self._kokoro is not None
        chunks = self._chunk_text(text)
        pieces: list[np.ndarray] = []
        sample_rate = SAMPLE_RATE_HZ
        for i, chunk in enumerate(chunks):
            samples, sample_rate = await asyncio.to_thread(
                self._kokoro.create, chunk, voice=voice, speed=1.0, lang="en-us"
            )
            pieces.append(samples)
            if i < len(chunks) - 1:
                pause = self._pause_between(chunk)
                if pause > 0:
                    pieces.append(np.zeros(int(pause * sample_rate), dtype=samples.dtype))
        if not pieces:
            return np.array([], dtype=np.float32), sample_rate
        return np.concatenate(pieces), sample_rate

    @staticmethod
    def _pause_between(chunk: str) -> float:
        stripped = chunk.strip()
        tail = stripped[-1] if stripped else ""
        if tail in ".?!":
            return PAUSE_SENTENCE
        if tail in ";:":
            return PAUSE_SEMI
        return PAUSE_COMMA

    @staticmethod
    def _chunk_text(text: str, max_chars: int = MAX_TTS_CHUNK_CHARS) -> list[str]:
        text = text.strip()
        if not text:
            return []
        sentences = re.split(r"(?<=[.!?;:])\s+", text)
        chunks: list[str] = []
        current = ""
        for sent in sentences:
            if len(sent) > max_chars:
                if current:
                    chunks.append(current)
                    current = ""
                for i in range(0, len(sent), max_chars):
                    chunks.append(sent[i:i + max_chars])
                continue
            if current and len(current) + 1 + len(sent) > max_chars:
                chunks.append(current)
                current = sent
            else:
                current = f"{current} {sent}" if current else sent
        if current:
            chunks.append(current)
        return chunks


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

    def _segment_text(self, text: str) -> list[tuple]:
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

        # Scripture references, years, then remaining small numbers.
        text = self._expand_scripture_refs(text)
        text = self._expand_years(text)
        text = self._expand_small_numbers(text)

        return text

    def _expand_scripture_refs(self, text: str) -> str:
        """Expand Bible references so they read naturally aloud.

        Examples:
          John 3:16            -> John chapter 3, verse 16
          John 3:16-17         -> John chapter 3, verses 16 through 17
          2 Timothy 3:16-17    -> Second Timothy chapter 3, verses 16 through 17
          Psalm 139            -> Psalm chapter 139
          Luke 11:1b           -> Luke chapter 11, verse 1
        """
        books = "|".join(sorted(BIBLE_BOOKS, key=len, reverse=True))
        pattern = re.compile(
            rf"\b({books})\s+(\d{{1,3}})"
            rf"(?::(\d{{1,3}})(?:[-–—](\d{{1,3}}))?([a-zA-Z]?))?"  # noqa: RUF001
        )

        def _repl(m: re.Match) -> str:
            book = m.group(1)
            chapter = m.group(2)
            verse = m.group(3)
            verse_end = m.group(4)

            parts = book.split(" ", 1)
            if parts[0].isdigit():
                book = _BOOK_ORDINAL.get(parts[0], parts[0]) + (" " + parts[1] if len(parts) > 1 else "")

            ch = _num_to_words(int(chapter))
            if verse:
                v = _num_to_words(int(verse))
                if verse_end:
                    ve = _num_to_words(int(verse_end))
                    return f"{book} chapter {ch}, verses {v} through {ve}"
                return f"{book} chapter {ch}, verse {v}"
            return f"{book} chapter {ch}"

        return pattern.sub(_repl, text)

    def _expand_years(self, text: str) -> str:
        """Read years and BC/AD/CE eras naturally: '1400 BC' -> 'fourteen hundred B.C.'."""
        # Era-suffixed years first (BC/AD/BCE/CE with optional dots).
        text = re.sub(
            r"\b(\d{1,4})\s*(B\.?\s?C\.?|A\.?\s?D\.?|BCE?|CE)\b",
            lambda m: f"{_year_to_words(int(m.group(1)))} {_ERA_WORDS.get(m.group(2).upper(), m.group(2).upper())}",
            text,
            flags=re.IGNORECASE,
        )
        # Standalone 4-digit years (1000-2999), not part of a larger number.
        text = re.sub(
            r"(?<!\d)(1\d{3}|20\d{2})(?!\d)",
            lambda m: _year_to_words(int(m.group(1))),
            text,
        )
        # Era words end in a dot ("B.C."), so "100 AD." would produce "A.D..".
        # Collapse the doubled period (but never touch an ellipsis).
        text = re.sub(r"(?<!\.)\.(\.)(?!\.)", ".", text)
        return text

    def _expand_small_numbers(self, text: str) -> str:
        """Convert standalone small integers to words so counts read naturally."""
        return re.sub(
            r"(?<![\w:./-])(\d{1,3})(?![\w:./-])",
            lambda m: _num_to_words(int(m.group(1))),
            text,
        )

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



async def text_to_speech(text: str, voice: str | None = None, storybook: bool = False) -> bytes:
    """Helper to generate audio bytes from text."""
    engine = get_tts_engine()
    return await engine.generate(text, voice, storybook=storybook)

