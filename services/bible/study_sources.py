"""What a study answer can draw on besides the passage, its chapter and its notes.

Three sources, gathered at once before the model is asked:

* **Related Scripture** from the whole Bible. The New Testament leans on the
  Old at every turn (quotations, types, promises), so a short first question
  asks the model which passages bear on this one, and the references the notes,
  the library and the web cite are added. Each is looked up in the family's own
  corpus and only its real text is given, so a reference that does not exist is
  dropped and nothing is quoted from memory.
* **The family library** (the indexed Calibre books: commentaries, sermons,
  devotional works), a few passages with title, author and chapter.
* **The web**, a search for how the passage is traditionally understood by
  Christians, as titles, addresses and snippets.

The gateway's own retrieval stays off for a study answer (``grounded``): the
model gets these sources already labelled instead of searching for itself,
which is what once answered a question about John R. Macduff with Macbeth.
Every source is best effort: one that fails or is slow is left out, never the
answer.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from sqlmodel import Session

from . import corpus, study_ask
from .refs import ReferenceError, VerseSpan, parse_reference

log = logging.getLogger("bible.study_sources")

MAX_RELATED_REFS = 10
MAX_VERSES_PER_REF = 6
MAX_RELATED_VERSES = 45
MAX_LIBRARY_PASSAGES = 4
MAX_LIBRARY_CHARACTERS = 900
MAX_WEB_RESULTS = 6
LIBRARY_TIMEOUT = 15.0
WEB_TIMEOUT = 15.0
SUGGEST_TIMEOUT = 60.0

SUGGEST_SYSTEM = (
    "You list Bible cross-references. Given a passage and a question about it, "
    "reply with up to eight references to other passages anywhere in the Bible "
    "that best help answer it: Old Testament texts it quotes, alludes to or "
    "fulfils, parallel accounts, and passages that explain the same teaching. "
    "Reply with the references only, one per line, like 'Isaiah 53:4-6'. No "
    "other words."
)


async def gather(
    *,
    session: Session,
    version: str,
    reference: str,
    question: str,
    verses: list[dict],
    notes: list[dict],
    context: list[dict],
    user: str,
    gateway_url: str,
    rag_url: str,
    execution_url: str,
    internal_secret: str,
) -> dict:
    """Related Scripture, library passages and web results for one question."""
    headers = {"X-Internal-Secret": internal_secret} if internal_secret else {}
    passage = "\n".join(f"{v.get('reference', '')} {v.get('text', '')}".strip() for v in verses[:30])
    suggested, library, web = await asyncio.gather(
        _suggest(reference, passage, question, user, gateway_url, internal_secret),
        _library(rag_url, headers, user, reference, question),
        _web(execution_url, headers, user, reference, question),
    )
    cited: list[str] = list(suggested)
    for text in [n.get("body", "") for n in notes] + [p["text"] for p in library] + [w["snippet"] for w in web]:
        cited.extend(study_ask.find_references(str(text or "")))
    shown = {(v.get("osis"), v.get("chapter"), v.get("verse")) for v in [*verses, *context]}
    related = _related(session, version, cited, shown)
    return {"related": related, "library": library, "web": web}


async def _suggest(reference: str, passage: str, question: str, user: str, gateway_url: str, secret: str) -> list[str]:
    prompt = f"Passage: {reference}\n{passage}\n\nQuestion: {question}"
    try:
        reply = await study_ask.ask(
            prompt=prompt, user=user, gateway_url=gateway_url, internal_secret=secret,
            timeout=SUGGEST_TIMEOUT, system=SUGGEST_SYSTEM,
        )
    except (study_ask.StudyAskUnavailable, study_ask.StudyAskError) as exc:
        log.info("cross-references not suggested: %s", exc)
        return []
    return study_ask.find_references(reply)


def _related(session: Session, version: str, refs: list[str], shown: set) -> list[dict]:
    """The real text of each reference, once each, skipping verses already given."""
    out: list[dict] = []
    seen: set = set(shown)
    total = 0
    for ref in dict.fromkeys(refs):
        if len(out) >= MAX_RELATED_REFS or total >= MAX_RELATED_VERSES:
            break
        try:
            spans = [sp for sp in parse_reference(ref) if not sp.whole_book]
            if not spans:
                continue
            rows = corpus.fetch_passage(session, version, [_short(s) for s in spans])
        except (ReferenceError, ValueError, LookupError):
            continue
        fresh = [r for r in rows if (r.get("osis"), r.get("chapter"), r.get("verse")) not in seen]
        fresh = fresh[: min(MAX_VERSES_PER_REF, MAX_RELATED_VERSES - total)]
        if not fresh:
            continue
        seen.update((r.get("osis"), r.get("chapter"), r.get("verse")) for r in fresh)
        total += len(fresh)
        out.append({"reference": ref, "verses": fresh})
    return out


def _short(span: VerseSpan) -> VerseSpan:
    """A whole-chapter reference trimmed to its opening verses."""
    if span.verse_end is None and span.resolved_chapter_end == span.chapter_start:
        return VerseSpan(book=span.book, chapter_start=span.chapter_start, verse_start=span.verse_start,
                         chapter_end=span.chapter_start, verse_end=span.verse_start + MAX_VERSES_PER_REF - 1)
    return span


async def _library(rag_url: str, headers: dict, user: str, reference: str, question: str) -> list[dict]:
    if not rag_url:
        return []
    body = {"user_id": user, "query": f"{reference} {question}"[:400], "collection_name": "calibre_files",
            "k": MAX_LIBRARY_PASSAGES}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=LIBRARY_TIMEOUT)) as s, \
                s.post(f"{rag_url.rstrip('/')}/rag/search", json=body, headers=headers) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        log.info("library not searched: %s", exc)
        return []
    out: list[dict] = []
    for hit in (data or {}).get("results") or []:
        meta = hit.get("metadata") or {}
        text = str(hit.get("content") or "").strip()
        if not text:
            continue
        out.append({
            "title": str(meta.get("title") or meta.get("name") or "A library book"),
            "author": str(meta.get("author") or ""),
            "chapter": str(meta.get("chapter") or ""),
            "text": text[:MAX_LIBRARY_CHARACTERS],
        })
    return out[:MAX_LIBRARY_PASSAGES]


async def _web(execution_url: str, headers: dict, user: str, reference: str, question: str) -> list[dict]:
    if not execution_url:
        return []
    body = {"user_context": {"user": user}, "query": f"{reference} {question} Christian commentary"[:300],
            "max_results": MAX_WEB_RESULTS, "safesearch": 1}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=WEB_TIMEOUT)) as s, \
                s.post(f"{execution_url.rstrip('/')}/execute/web_search", json=body, headers=headers) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        log.info("web not searched: %s", exc)
        return []
    results = ((data or {}).get("detail") or {}).get("results") or []
    out = []
    for r in results:
        snippet = str(r.get("snippet") or "").strip()
        if snippet:
            out.append({"title": str(r.get("title") or ""), "url": str(r.get("url") or ""), "snippet": snippet[:400]})
    return out[:MAX_WEB_RESULTS]
