"""Read an imported study section aloud, the way Scripture is read aloud.

Same contract as ``narration``: the script is built here from the stored
section, never from text the client sends, so the speech engine cannot be
used as a "say anything" service. Same engine and cache too: pieces are
spoken by ``services.execution`` (Kokoro or Piper through the alpaca audio
server, with its scripture-aware text normalisation, so "Romans 8:28" is read
as "Romans chapter 8, verse 28") and stored in ``NarrationAudio`` under a key
that includes the piece's own words.

What a listener should hear is not the same as what a reader sees:

* headings are read, with a pause after them,
* footnotes, footnote markers, tables and images are skipped,
* discussion questions are announced as questions ("Question 2."),
* list items are read one by one.

Each piece says which blocks it speaks and where each starts in the piece's
text, so the reader can follow along paragraph by paragraph and start from
the paragraph on screen.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from sqlmodel import Session

from services.bible import narration
from services.bible.models import ImportedBook, ImportedBookChapter, NarrationAudio
from services.bible.study_blocks import Block
from services.bible.study_reformat import blocks_of

NarrationError = narration.NarrationError
NarrationUnavailable = narration.NarrationUnavailable

#: A very long section is still read, a piece at a time; past this it is refused.
MAX_CHARACTERS = 200_000
TARGET_CHUNK_CHARACTERS = narration.TARGET_CHUNK_CHARACTERS
FIRST_CHUNK_CHARACTERS = narration.FIRST_CHUNK_CHARACTERS

_SENTENCE = re.compile(r"(?<=[.!?\u201d\"])\s+(?=[\"\u201c(A-Z0-9])")
_MARKERS = re.compile(r"\[(?:\d{1,3}|[*\u2020\u2021])\]|[\u00b9\u00b2\u00b3\u2070-\u2079]+")
_SKIP_TYPES = {"note", "table", "image"}


@dataclass
class Spoken:
    block_id: str
    text: str


@dataclass
class Piece:
    index: int
    parts: list[Spoken] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(part.text for part in self.parts)

    def marks(self) -> list[dict]:
        out = []
        position = 0
        for part in self.parts:
            if not out or out[-1]["block_id"] != part.block_id:
                out.append({"block_id": part.block_id, "start": position, "end": position + len(part.text)})
            else:
                out[-1]["end"] = position + len(part.text)
            position += len(part.text) + 1
        return out


def _legacy_blocks(text: str) -> list[Block]:
    """A section imported before the normaliser: paragraphs are blank-line separated."""
    out = []
    for index, raw in enumerate(re.split(r"\n\s*\n", text or ""), start=1):
        joined = re.sub(r"\s+", " ", re.sub(r"(\w)-\n(\w)", r"\1\2", raw)).strip()
        if joined and not re.fullmatch(r"\d{1,4}", joined):
            out.append(Block(type="paragraph", text=joined, id=f"p{index}"))
    return out


def spoken_blocks(chapter: ImportedBookChapter) -> list[Spoken]:
    blocks = blocks_of(chapter) or _legacy_blocks(chapter.text or "")
    out: list[Spoken] = []
    question = 0
    for block in blocks:
        if block.type in _SKIP_TYPES:
            continue
        if block.type == "list":
            for item in block.items:
                text = _MARKERS.sub("", item).strip()
                if text:
                    out.append(Spoken(block.id, text if re.search(r"[.!?:;]$", text) else text + "."))
            continue
        text = _MARKERS.sub("", block.text).strip()
        text = re.sub(r"\s+", " ", text)
        if not text:
            continue
        if block.type == "heading":
            text = text if re.search(r"[.!?:]$", text) else text + "."
        elif block.type == "question":
            question += 1
            text = f"Question {question}. {text}"
        out.append(Spoken(block.id, text))
    return out


def split(spoken: list[Spoken]) -> list[Piece]:
    """Pieces of about a minute, cut between blocks or sentences, the first one short."""
    sentences: list[Spoken] = []
    for item in spoken:
        if len(item.text) <= TARGET_CHUNK_CHARACTERS:
            sentences.append(item)
            continue
        for part in _SENTENCE.split(item.text):
            if part.strip():
                sentences.append(Spoken(item.block_id, part.strip()))
    pieces: list[Piece] = []
    current = Piece(index=0)
    size = 0
    limit = FIRST_CHUNK_CHARACTERS
    for sentence in sentences:
        if current.parts and size + len(sentence.text) + 1 > limit:
            pieces.append(current)
            current = Piece(index=len(pieces))
            size = 0
            limit = TARGET_CHUNK_CHARACTERS
        current.parts.append(sentence)
        size += len(sentence.text) + 1
    if current.parts:
        pieces.append(current)
    return pieces


def _source(book: ImportedBook, chapter: ImportedBookChapter) -> tuple[str, str]:
    """The cache's "translation" and "reference" for a section."""
    section = chapter.section_id or f"ordinal-{chapter.ordinal}"
    return f"book:{book.id}", section


def _key(book: ImportedBook, chapter: ImportedBookChapter, voice: str, piece: Piece) -> str:
    version, reference = _source(book, chapter)
    digest = hashlib.sha256(f"{version}|{reference}|{voice}|c{piece.index}|{piece.text}".encode()).hexdigest()
    return digest[:32]


def pieces_for(chapter: ImportedBookChapter) -> list[Piece]:
    spoken = spoken_blocks(chapter)
    if not spoken:
        raise NarrationError("This section has no text to read aloud.")
    total = sum(len(item.text) for item in spoken)
    if total > MAX_CHARACTERS:
        raise NarrationError(
            f"This section is {total:,} characters; reading aloud covers up to {MAX_CHARACTERS:,}."
        )
    return split(spoken)


def plan(session: Session, *, book: ImportedBook, chapter: ImportedBookChapter, voice: str | None) -> dict:
    key_voice = voice or "default"
    pieces = pieces_for(chapter)
    keys = {piece.index: _key(book, chapter, key_voice, piece) for piece in pieces}
    spoken = narration._cached_keys(session, list(keys.values()))
    _version, reference = _source(book, chapter)
    return {
        "book_id": book.id,
        "ordinal": chapter.ordinal,
        "section_id": reference,
        "voice": key_voice,
        "count": len(pieces),
        "characters": sum(len(p.text) for p in pieces),
        "cached_count": len(spoken),
        "all_cached": len(spoken) == len(pieces),
        "chunks": [
            {
                "index": piece.index,
                "characters": len(piece.text),
                "cached": keys[piece.index] in spoken,
                "blocks": piece.marks(),
            }
            for piece in pieces
        ],
    }


async def narrate_chunk(
    session: Session,
    *,
    book: ImportedBook,
    chapter: ImportedBookChapter,
    voice: str | None,
    index: int,
    execution_url: str,
    internal_secret: str,
    timeout: float = 180.0,
) -> dict:
    key_voice = voice or "default"
    pieces = pieces_for(chapter)
    if index < 0 or index >= len(pieces):
        raise NarrationError(f"This section is {len(pieces)} parts of narration, so there is no part {index}.")
    piece = pieces[index]
    key = _key(book, chapter, key_voice, piece)
    version, reference = _source(book, chapter)
    hit = session.get(NarrationAudio, key)
    cached = hit is not None and bool(hit.audio)
    if cached:
        assert hit is not None
        audio, mime = hit.audio, hit.mime_type or narration.AUDIO_MIME
    else:
        audio, mime = await narration._speak(
            piece.text,
            voice=voice,
            execution_url=execution_url,
            internal_secret=internal_secret,
            timeout=timeout,
        )
        row = session.get(NarrationAudio, key) or NarrationAudio(key=key)
        row.version_code = version
        row.reference = reference
        row.voice = key_voice
        row.chunk_index = piece.index
        row.mime_type = mime
        row.audio = audio
        row.created_at = narration.utcnow()
        session.add(row)
        session.commit()
    import base64

    return {
        "book_id": book.id,
        "ordinal": chapter.ordinal,
        "section_id": reference,
        "voice": key_voice,
        "index": index,
        "count": len(pieces),
        "characters": len(piece.text),
        "cached": cached,
        "blocks": piece.marks(),
        "mime_type": mime,
        "length_bytes": len(audio),
        "audio_base64": base64.b64encode(audio).decode("utf-8"),
    }
