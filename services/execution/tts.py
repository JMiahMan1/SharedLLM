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

# pykokoro (Kokoro ONNX) resolves the ~510-token cap internally by auto-splitting
# phoneme batches and re-joining segments with clause/sentence-level pauses, so
# no manual chunking with inter-chunk silence is needed here. Pause pacing is
# delegated to GenerationConfig(pause_mode="auto", ...); explicit structure
# beats (after titles/headers, list items, and scripture references) are mapped
# to an SSMD break of PAUSE_STRUCTURE seconds.
PAUSE_STRUCTURE = 0.55  # seconds of pause after titles/headers, list items, and references
PAUSE_CLAUSE = 0.3      # pause after clause-boundary splits (; : ,)
PAUSE_SENTENCE = 0.5    # pause after sentence boundaries (. ? !)
PAUSE_PARAGRAPH = 1.0   # pause between paragraphs
SAMPLE_RATE_HZ = 24000

# Internal marker used to flag narrator-structure boundaries (paragraph
# titles/headers, list items, and scripture references) in the text. It is
# inserted before normalization and split out again during synthesis, so it is
# NEVER passed to Kokoro (which would otherwise try to speak it).
_PAUSE_MARK = "\x02"


def _structure_break() -> str:
    """Return the SSMD break token for the narrator-structure beat."""
    return f"...{int(PAUSE_STRUCTURE * 1000)}ms"

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
    """Local-first TTS using Kokoro-v1.0 (ONNX) via pykokoro. Includes Storybook Mode logic."""
    def __init__(self, model_path: str = "", voices_path: str = ""):
        if not model_path:
            model_path = os.path.join(MODELS_DIR, "kokoro-v1.0.onnx")
        if not voices_path:
            voices_path = os.path.join(MODELS_DIR, "voices-v1.0.bin")
        self.model_path = model_path
        self.voices_path = voices_path
        self._pipeline = None

    def _ensure_loaded(self):
        if self._pipeline is None:
            from pykokoro import GenerationConfig, KokoroPipeline, PipelineConfig
            if not os.path.exists(self.model_path):
                log.error(f"Kokoro model not found at {self.model_path}")
                raise FileNotFoundError(f"Kokoro model missing: {self.model_path}")
            if not os.path.exists(self.voices_path):
                log.error(f"Kokoro voices not found at {self.voices_path}")
                raise FileNotFoundError(f"Kokoro voices missing: {self.voices_path}")
            config = PipelineConfig(
                voice=(DEFAULT_TTS_VOICE or "am_michael"),
                model_path=self.model_path,
                voices_path=self.voices_path,
                model_source="github",
                model_variant="v1.0",
                model_quality="fp32",
                provider="cpu",
                return_trace=False,
                retain_segment_audio=False,
                cache_dir=os.path.join(os.path.expanduser("~"), ".cache", "pykokoro"),
                generation=GenerationConfig(
                    pause_mode="auto",
                    pause_clause=PAUSE_CLAUSE,
                    pause_sentence=PAUSE_SENTENCE,
                    pause_paragraph=PAUSE_PARAGRAPH,
                ),
            )
            self._pipeline = KokoroPipeline(config)

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

        text = self._mark_structure_pauses(text)
        text = self._normalize_text(text)
        samples, sample_rate = await self._synthesize(text, voice)
        if len(samples) == 0:
            return b""
        return self._samples_to_bytes(samples, sample_rate)

    async def _synthesize(self, text: str, voice: str) -> tuple[np.ndarray, int]:
        """Synthesize text with the pykokoro pipeline.

        pykokoro auto-splits long inputs at the model's ~510-phoneme cap and
        re-joins the pieces with clause-level pauses, so long texts no longer
        need manual chunking with spliced silence. Structured narration beats
        (titles, list items, scripture references) are inserted as SSMD breaks
        so they read as deliberate pauses instead of robotic gaps.
        """
        assert self._pipeline is not None
        # Map the internal structure marker to an explicit SSMD break so the
        # beat survives the pipeline (it would otherwise be phonemized).
        text = text.replace(_PAUSE_MARK, _structure_break())
        result = await asyncio.to_thread(self._pipeline.run, text, voice=voice)
        samples = np.asarray(result.audio, dtype=np.float32)
        sample_rate = int(result.sample_rate)
        if len(samples) == 0:
            return np.array([], dtype=np.float32), SAMPLE_RATE_HZ
        return samples, sample_rate


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
            if not normalized.strip():
                continue

            assert self._pipeline is not None
            result = await asyncio.to_thread(self._pipeline.run, normalized, voice=voice)
            all_samples.append(np.asarray(result.audio, dtype=np.float32))
            last_sample_rate = int(result.sample_rate)

        if not all_samples:
            return b""
        combined = np.concatenate(all_samples)
        return self._samples_to_bytes(combined, last_sample_rate)

    def _segment_text(self, text: str) -> list[tuple]:
        """Splits text into (content, is_dialogue) tuples."""
        parts = re.split(r'("[^"]+")', text)
        result = []
        for p in parts:
            if not p.strip():
                continue
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

    def _mark_structure_pauses(self, text: str) -> str:
        """Insert narrator-structure pause markers around structural units.

        Runs on the RAW text (before normalization) so the original headings,
        list markup and references are still visible, then inserts _PAUSE_MARK
        so _synthesize can drop a slightly longer beat after:
          * paragraph headers / titles  (e.g. "Week 2: Scripture", "Chapter One.")
          * each item in an enumerated / bulleted list
          * a scripture reference (e.g. "(Luke 11:1b)")
        The marker survives normalization untouched and is stripped (never
        spoken) during synthesis.
        """
        # 1) Append a marker after every scripture reference (plus a closing
        #    paren if present), even inline: "Genesis 1:1 says, ..." reads as
        #    "Genesis chapter one, verse one [beat] says, ...".
        text = self._append_ref_markers(text)

        # 2) Line-based structure: titles/headers, enumerated+bulleted lists,
        #    and indented poetic/centered lines (e.g. the Lord's Prayer block).
        lines = text.split("\n")
        out: list[str] = []
        prev_stripped = ""
        for line in lines:
            stripped = line.strip()
            if not stripped:
                out.append(line)
                prev_stripped = ""
                continue

            is_title = (
                len(stripped) <= 80
                and (
                    re.match(r"^(Chapter|Week|Day|Part|Section|Lesson)\b", stripped, re.I)
                    or (
                        not re.search(r"[.!?]\s*$", stripped)
                        and (not prev_stripped and len(stripped) <= 60)
                    )
                )
            )
            is_list_item = bool(re.match(r"^\s*(?:\d{1,3}[.)]\s|[-•*]\s)", stripped))
            is_poem_line = bool(
                not is_title
                and not is_list_item
                and line.startswith((" ", "\t"))
                and len(stripped) <= 60
                and not re.search(r"[.!?]\s*$", stripped)
            )

            if is_title or is_list_item or is_poem_line:
                out.append(line + _PAUSE_MARK)
                prev_stripped = stripped
                continue

            out.append(line)
            prev_stripped = stripped

        return "\n".join(out)

    def _append_ref_markers(self, text: str) -> str:
        """Append _PAUSE_MARK after each scripture reference occurrence."""
        ref_re = self._scripture_ref_re()
        pieces: list[str] = []
        last = 0
        for m in ref_re.finditer(text):
            pieces.append(text[last:m.end()])
            last = m.end()
            # Fold a closing parenthesis into the marked unit so we never leave
            # a bare ")" fragment behind for Kokoro to synthesize.
            if last < len(text) and text[last] == ")":
                pieces.append(text[last])
                last += 1
            pieces.append(_PAUSE_MARK)
        pieces.append(text[last:])
        return "".join(pieces)

    def _scripture_ref_re(self) -> "re.Pattern[str]":
        books = "|".join(sorted(BIBLE_BOOKS, key=len, reverse=True))
        return re.compile(
            rf"\b({books})\s+(\d{{1,3}})"
            rf"(?::(\d{{1,3}})(?:[-–—](\d{{1,3}}))?([a-zA-Z]?))?"  # noqa: RUF001
        )

    def _expand_scripture_refs(self, text: str) -> str:
        """Expand Bible references so they read naturally aloud.

        Examples:
          John 3:16            -> John chapter 3, verse 16
          John 3:16-17         -> John chapter 3, verses 16 through 17
          2 Timothy 3:16-17    -> Second Timothy chapter 3, verses 16 through 17
          Psalm 139            -> Psalm chapter 139
          Luke 11:1b           -> Luke chapter 11, verse 1
        """
        pattern = self._scripture_ref_re()

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

