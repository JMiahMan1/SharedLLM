# services/bible/meaning.py
"""Search by meaning: "verses about worry" finds Matthew 6:25 and Philippians 4:6.

Every verse of one translation is embedded once by the RAG service's model
(``POST /rag/embed``), and the vectors are kept here, in ``verse_vector``,
not in a RAG collection: a collection would surface 31,000 verses in every
household knowledge search. Verse numbering lines up across translations, so
the results are shown in whichever translation the reader has open.

A meaning search blends two rankings with reciprocal rank fusion: the closest
verses by cosine similarity, and the word index (``search.py``) over the
query's content words. The blend keeps an exact word match near the top while
letting a verse that never uses the word ("Be careful for nothing", KJV
Phil 4:6) still be found.

The embedding job runs in the background, resumes where it stopped, embeds
only missing verses, and redoes everything if the model changes. Until it has
finished, meaning search answers from the vectors it has and says so
(``meaning_coverage``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import numpy as np
from sqlalchemy import text
from sqlmodel import Field, Session, SQLModel

from services.bible.books import BOOK_BY_OSIS, BOOK_ORDER

log = logging.getLogger(__name__)

#: Verses per /rag/embed call. After each call the job rests at least as long
#: as the call took: the server has four cores shared by every service, and a
#: first pass (about 40 minutes of embedding on it) must not slow the house.
BATCH = 64
BATCH_PAUSE_S = 0.2
#: How often the job looks for verses without vectors (a re-import, a new
#: translation chosen) once it has caught up, and after a failure.
RECHECK_S = 3600
RETRY_S = 120
EMBED_TIMEOUT_S = 60
QUERY_TIMEOUT_S = 15
#: How many of the closest verses take part in the blend.
CANDIDATES = 300
RRF_K = 60
#: A verse whose similarity is this far below the best one is not offered:
#: without a floor, a meaning search returns the whole Bible in some order.
SCORE_WINDOW = 0.12
MAX_RESULTS = 200
#: The blend's best this many verses are read again, together with the
#: question, by the RAG service's cross-encoder (/rag/rerank), which orders
#: them by how well each answers it. A verse that only shares a word ("hurt")
#: or a mood ("fear the LORD" for worry) with the question sinks.
RERANK_CANDIDATES = 50
#: A reranked verse scoring this far below the best one is off topic and is
#: left out (the scores are the cross-encoder's logits). Measured on the live
#: Bible: on-topic verses sat within about 5 of the best, the ones people
#: called off topic 6 or more below.
RERANK_WINDOW = 6.0
#: Never fewer than this many, however low the scores: a question the model
#: finds hard still gets its best few answers.
RERANK_MIN_RESULTS = 5
RERANK_TIMEOUT_S = 10

#: Translations whose wording is closest to how people ask today, in order,
#: for when ``BIBLE_MEANING_VERSION`` is not set.
PREFERRED = ("esv", "niv", "nlt", "csb", "nasb", "web", "nkjv", "kjv", "asv")

#: Words that carry no meaning in a request ("verses about worry"); left out
#: of the word half of the blend so they do not rank every verse.
STOPWORDS = frozenset(
    [
        "a", "about", "after", "again", "all", "also", "am", "an", "and", "any", "are", "as", "at", "be", "because",
        "been", "being", "but", "by", "can", "could", "did", "do", "does", "doing", "for", "from", "had", "has",
        "have", "having", "he", "her", "here", "hers", "him", "his", "how", "i", "if", "in", "into", "is", "it",
        "its", "me", "more", "most", "my", "no", "nor", "not", "of", "on", "once", "only", "or", "other", "our",
        "out", "over", "own", "same", "she", "should", "so", "some", "such", "than", "that", "the", "their", "them",
        "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "very",
        "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with",
        "would", "you", "your", "verse", "verses", "bible", "scripture", "scriptures", "passage", "passages", "say",
        "says", "said", "talk", "talks", "talking", "show", "find", "tell", "god's",
        # Pronoun-like words carry no topic, but match thousands of verses.
        "someone", "somebody", "something", "anyone", "anybody", "anything", "everyone", "everybody",
        "everything"
    ]
)


class VerseVector(SQLModel, table=True):
    """One verse's embedding (float16, little-endian) from ``model``."""

    __tablename__ = "verse_vector"
    __table_args__ = {"extend_existing": True}
    version_code: str = Field(primary_key=True)
    osis: str = Field(primary_key=True)
    chapter: int = Field(primary_key=True)
    verse: int = Field(primary_key=True)
    model: str = Field(default="", index=True)
    vec: bytes = Field(default=b"")


class MeaningUnavailable(RuntimeError):
    """Meaning search cannot run; the message says why and what fixes it."""


# ---------------------------------------------------------------------------
# The RAG service's embedder


def _post_embed(rag_url: str, secret: str, texts: list[str], kind: str, timeout: float) -> dict:
    body = json.dumps({"texts": texts, "kind": kind}).encode()
    request = urllib.request.Request(
        f"{rag_url.rstrip('/')}/rag/embed",
        data=body,
        headers={"Content-Type": "application/json", "X-Internal-Secret": secret},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read())


def embed_query(rag_url: str, secret: str, query: str) -> tuple[str, np.ndarray]:
    try:
        out = _post_embed(rag_url, secret, [query[:2000]], "query", QUERY_TIMEOUT_S)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise MeaningUnavailable(f"Meaning search could not reach the embedding model: {exc}") from exc
    vec = np.asarray(out["vectors"][0], dtype=np.float32)
    norm = float(np.linalg.norm(vec)) or 1.0
    return str(out["model"]), vec / norm


def _pack(vector: list[float]) -> bytes:
    arr = np.asarray(vector, dtype=np.float32)
    arr = arr / (float(np.linalg.norm(arr)) or 1.0)
    return arr.astype("<f2").tobytes()


# ---------------------------------------------------------------------------
# Which translation is embedded


def choose_version(installed: list[str], configured: str | None) -> str | None:
    """The configured translation when installed, else the first of PREFERRED."""
    if configured:
        code = configured.strip().lower()
        if code in installed:
            return code
        log.warning("[Bible] BIBLE_MEANING_VERSION=%s is not installed; choosing another", code)
    for code in PREFERRED:
        if code in installed:
            return code
    return installed[0] if installed else None


# ---------------------------------------------------------------------------
# The background job


def missing_verses(session: Session, version: str, model: str, limit: int) -> list[tuple[str, int, int, str]]:
    rows = session.connection().execute(
        text(
            "SELECT v.osis, v.chapter, v.verse, v.text FROM bibleverse v "
            "LEFT JOIN verse_vector x ON x.version_code = v.version_code AND x.osis = v.osis "
            "AND x.chapter = v.chapter AND x.verse = v.verse AND x.model = :model "
            "WHERE v.version_code = :version AND x.osis IS NULL AND trim(v.text) != '' LIMIT :limit"
        ),
        {"version": version, "model": model, "limit": limit},
    ).all()
    return [(r[0], int(r[1]), int(r[2]), str(r[3])) for r in rows]


def embed_pass(engine, *, version: str, rag_url: str, secret: str, pause: float = BATCH_PAUSE_S) -> int:
    """Embed every verse of ``version`` that has no vector from the current model.

    Returns how many were embedded. Vectors from another model, or for a
    translation no longer chosen, are deleted first so the index never mixes
    two kinds of vector.
    """
    probe = _post_embed(rag_url, secret, ["In the beginning"], "document", EMBED_TIMEOUT_S)
    model = str(probe["model"])
    with Session(engine) as session:
        conn = session.connection()
        stale = conn.execute(
            text("DELETE FROM verse_vector WHERE model != :model OR version_code != :version"),
            {"model": model, "version": version},
        ).rowcount
        session.commit()
        if stale:
            log.info("[Bible] meaning index: dropped %s vectors from another model or translation", stale)
    done = 0
    while True:
        with Session(engine) as session:
            batch = missing_verses(session, version, model, BATCH)
        if not batch:
            break
        started = time.monotonic()
        out = _post_embed(rag_url, secret, [row[3] for row in batch], "document", EMBED_TIMEOUT_S)
        took = time.monotonic() - started
        if str(out["model"]) != model:
            raise MeaningUnavailable("The embedding model changed during indexing; starting again")
        with Session(engine) as session:
            for (osis, chapter, verse, _text), vector in zip(batch, out["vectors"], strict=True):
                session.merge(VerseVector(version_code=version, osis=osis, chapter=chapter, verse=verse,
                                          model=model, vec=_pack(vector)))
            session.commit()
        done += len(batch)
        if done % (BATCH * 50) == 0:
            log.info("[Bible] meaning index: %s verses embedded so far", done)
        if pause:
            time.sleep(max(pause, took))
    if done:
        log.info("[Bible] meaning index: embedded %s verses of %s", done, version)
    _cache.invalidate()
    return done


async def index_loop(engine_factory, *, installed_versions, configured: str | None, rag_url: str, secret: str) -> None:
    """Keep the meaning index complete: on start, then hourly, and after a failure."""
    while True:
        wait = RECHECK_S
        try:
            engine = engine_factory()
            version = choose_version(installed_versions(), configured)
            if version:
                await asyncio.to_thread(embed_pass, engine, version=version, rag_url=rag_url, secret=secret)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the RAG service restarting is routine; try again soon
            log.warning("[Bible] meaning index paused: %s", exc)
            wait = RETRY_S
        await asyncio.sleep(wait)


# ---------------------------------------------------------------------------
# The in-memory matrix


@dataclass
class _Matrix:
    key: tuple
    version: str
    model: str
    positions: list[tuple[str, int, int]]
    vectors: np.ndarray


class _Cache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._matrix: _Matrix | None = None

    def invalidate(self) -> None:
        with self._lock:
            self._matrix = None

    def get(self, session: Session, model: str) -> _Matrix | None:
        conn = session.connection()
        row = conn.execute(
            text("SELECT version_code, count(*) FROM verse_vector WHERE model = :m GROUP BY version_code "
                 "ORDER BY count(*) DESC LIMIT 1"),
            {"m": model},
        ).first()
        if not row:
            return None
        key = (str(session.get_bind().url), model, row[0], int(row[1]))
        with self._lock:
            if self._matrix is not None and self._matrix.key == key:
                return self._matrix
        rows = conn.execute(
            text("SELECT osis, chapter, verse, vec FROM verse_vector WHERE model = :m AND version_code = :v"),
            {"m": model, "v": row[0]},
        ).all()
        positions = [(r[0], int(r[1]), int(r[2])) for r in rows]
        vectors = np.vstack([np.frombuffer(r[3], dtype="<f2") for r in rows]).astype(np.float32)
        matrix = _Matrix(key=key, version=str(row[0]), model=model, positions=positions, vectors=vectors)
        with self._lock:
            self._matrix = matrix
        return matrix


_cache = _Cache()


def coverage(session: Session, version: str) -> float:
    conn = session.connection()
    total = conn.execute(text("SELECT count(*) FROM bibleverse WHERE version_code = :v AND trim(text) != ''"),
                         {"v": version}).scalar() or 0
    have = conn.execute(text("SELECT count(*) FROM verse_vector WHERE version_code = :v"), {"v": version}).scalar() or 0
    return round(min(1.0, have / total), 3) if total else 0.0


# ---------------------------------------------------------------------------
# Searching


def content_words(query: str) -> list[str]:
    import re

    words = re.findall(r"[A-Za-z']+", query.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def closest(
    session: Session, query: str, *, rag_url: str, secret: str, books: tuple[str, ...] | None = None
) -> tuple[list[tuple[tuple[str, int, int], float]], _Matrix]:
    """The verses closest in meaning to ``query``, best first, with cosine scores."""
    model, vector = embed_query(rag_url, secret, query)
    matrix = _cache.get(session, model)
    if matrix is None:
        raise MeaningUnavailable(
            "Meaning search is not ready yet: the verses are still being prepared. Try again in a few minutes."
        )
    scores = matrix.vectors @ vector
    if books is not None:
        allowed = set(books)
        mask = np.fromiter((p[0] in allowed for p in matrix.positions), dtype=bool, count=len(matrix.positions))
        scores = np.where(mask, scores, -1.0)
    take = min(CANDIDATES, len(scores))
    top = np.argpartition(-scores, take - 1)[:take]
    top = top[np.argsort(-scores[top])]
    best = float(scores[top[0]]) if take else 0.0
    out = [
        (matrix.positions[i], float(scores[i]))
        for i in top
        if scores[i] > -1.0 and float(scores[i]) >= best - SCORE_WINDOW
    ]
    return out, matrix


def fuse(meaning: list[tuple[str, int, int]], words: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Reciprocal rank fusion of the two rankings, best first."""
    score: dict[tuple[str, int, int], float] = {}
    for rank, pos in enumerate(meaning):
        score[pos] = score.get(pos, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, pos in enumerate(words):
        # Word matches outside the meaning candidates are kept only when they
        # are near the top of the word ranking: a stray common word should not
        # pull in verses the model thought unrelated.
        if pos in score or rank < 20:
            score[pos] = score.get(pos, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(score, key=lambda p: (-score[p], BOOK_ORDER.get(p[0], 99), p[1], p[2]))


class RerankUnavailable(RuntimeError):
    """The reranker could not answer; the blend's own order is used instead."""


def _post_rerank(rag_url: str, secret: str, query: str, texts: list[str], timeout: float) -> dict:
    body = json.dumps({"query": query[:2000], "texts": [t[:2000] for t in texts]}).encode()
    request = urllib.request.Request(
        f"{rag_url.rstrip('/')}/rag/rerank",
        data=body,
        headers={"Content-Type": "application/json", "X-Internal-Secret": secret},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return json.loads(resp.read())


def rerank_scores(rag_url: str, secret: str, query: str, texts: list[str]) -> list[float]:
    """The cross-encoder's score for each text against ``query``, in order."""
    try:
        answer = _post_rerank(rag_url, secret, query, texts, RERANK_TIMEOUT_S)
    except Exception as exc:
        raise RerankUnavailable(f"the reranker did not answer ({exc.__class__.__name__}: {exc})") from exc
    scores = answer.get("scores") if isinstance(answer, dict) else None
    if not isinstance(scores, list) or len(scores) != len(texts):
        raise RerankUnavailable("the reranker answered with the wrong number of scores")
    return [float(x) for x in scores]


def rerank_order(
    positions: list[tuple[str, int, int]], scores: list[float]
) -> list[tuple[str, int, int]]:
    """``positions`` best answer first, without the ones far below the best.

    Ties keep the blend's order. At least ``RERANK_MIN_RESULTS`` are kept.
    """
    if not positions:
        return []
    order = sorted(range(len(positions)), key=lambda i: (-scores[i], i))
    best = scores[order[0]]
    kept = [positions[i] for n, i in enumerate(order) if n < RERANK_MIN_RESULTS or scores[i] >= best - RERANK_WINDOW]
    return kept


def describe_position(pos: tuple[str, int, int]) -> str:
    name = BOOK_BY_OSIS.get(pos[0], {}).get("name", pos[0])
    return f"{name} {pos[1]}:{pos[2]}"


def ensure_table(engine) -> None:
    VerseVector.__table__.create(engine, checkfirst=True)  # type: ignore[attr-defined]


__all__ = [
    "MeaningUnavailable",
    "VerseVector",
    "choose_version",
    "closest",
    "content_words",
    "coverage",
    "embed_pass",
    "embed_query",
    "ensure_table",
    "fuse",
    "index_loop",
]
