"""An optional, bounded second opinion on structure from the household's local model.

The rule-based readers are right about most lines. Where they cannot tell (a
short line with no punctuation could be a subheading or the end of a verse
quotation), this pass asks the local model on alpaca to *label* those lines.
It is built so that it cannot change the book's words:

* the model only ever returns block ids and a label from a fixed set
  ("heading" or "paragraph"); text is never taken from its answer,
* only the ambiguous candidates are sent, at most ``MAX_CANDIDATES`` of them,
  each cut to ``MAX_CHARS``, with one request and a timeout,
* after the labels are applied the book's :func:`text_fingerprint` is compared
  with the one before, and any difference discards the whole pass.

It runs only when an administrator asks for it, and only against the local
model URL (``llm_local_url``): household books never go to a cloud model. With
no local model configured it reports that it was skipped.
"""
from __future__ import annotations

import json
import logging
import re

import aiohttp

from services.bible.study_blocks import Document, section_text, text_fingerprint

log = logging.getLogger(__name__)

MAX_CANDIDATES = 80
MAX_CHARS = 140
TIMEOUT_S = 90

_PROMPT = (
    "You label lines extracted from a Bible study book. For each numbered line, "
    "answer whether it is a HEADING (a title or subheading of the text that follows) "
    "or a PARAGRAPH (prose, a quotation, or part of a sentence). Reply with JSON only: "
    '{"headings": [ids of the lines that are headings]}. Do not repeat or rewrite the lines.'
)


def candidates(document: Document) -> list[dict]:
    """Short standalone paragraphs and weak (bold-only) headings: the unsure cases."""
    out: list[dict] = []
    for section in document.sections:
        for index, block in enumerate(section.blocks):
            if len(out) >= MAX_CANDIDATES:
                return out
            unsure = (
                (block.type == "paragraph" and len(block.text.split()) <= 10 and not re.search(r"[.!?:;,\"\u201d]$", block.text))
                or (block.type == "heading" and block.level >= 5)
            )
            if not unsure:
                continue
            after = next((b.text for b in section.blocks[index + 1:] if b.text), "")
            out.append({
                "id": block.id,
                "line": block.text[:MAX_CHARS],
                "next": after[:80],
                "current": block.type,
            })
    return out


def apply_labels(document: Document, headings: set[str], considered: set[str]) -> int:
    before = text_fingerprint("".join(section_text(s) for s in document.sections))
    changes: list[tuple] = []
    for section in document.sections:
        for block in section.blocks:
            if block.id not in considered:
                continue
            wanted = "heading" if block.id in headings else "paragraph"
            if wanted != block.type:
                changes.append((block, block.type, block.level))
                block.type = wanted
                block.level = 4 if wanted == "heading" else 0
    after = text_fingerprint("".join(section_text(s) for s in document.sections))
    if before != after:
        for block, kind, level in changes:
            block.type, block.level = kind, level
        raise ValueError("the labelled book no longer has the same words; the pass was discarded")
    return len(changes)


async def assist(document: Document, *, llm_url: str, model: str) -> dict:
    """Ask the local model about the unsure lines. Returns a report; never raises for the model's sake."""
    if not llm_url:
        return {"status": "skipped", "reason": "no local model is configured (llm_local_url)"}
    if not model:
        return {"status": "skipped", "reason": "no default model is configured"}
    found = candidates(document)
    if not found:
        return {"status": "skipped", "reason": "nothing was ambiguous"}
    numbered = "\n".join(
        f"{n}. {c['line']}" + (f"   (followed by: {c['next']})" if c["next"] else "")
        for n, c in enumerate(found, start=1)
    )
    body = {
        "model": model,
        "stream": False,
        "format": "json",
        "think": False,
        "options": {"temperature": 0, "num_predict": 400},
        "messages": [
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": numbered},
        ],
    }
    try:
        async with aiohttp.ClientSession() as client, client.post(
            f"{llm_url.rstrip('/')}/api/chat",
            json=body,
            headers={"X-Request-Source": "bible-study-import"},
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
        ) as resp:
            if resp.status >= 400:
                return {"status": "failed", "reason": f"the local model answered HTTP {resp.status}"}
            payload = await resp.json(content_type=None)
    except Exception as exc:
        return {"status": "failed", "reason": f"the local model could not be reached: {exc}"}
    content = str(((payload or {}).get("message") or {}).get("content") or "")
    match = re.search(r"\{.*\}", content, re.S)
    try:
        parsed = json.loads(match.group(0)) if match else {}
    except (ValueError, TypeError):
        parsed = {}
    picked: set[str] = set()
    for value in parsed.get("headings") or []:
        try:
            index = int(value) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(found):
            picked.add(found[index]["id"])
    try:
        changed = apply_labels(document, picked, {c["id"] for c in found})
    except ValueError as exc:
        return {"status": "discarded", "reason": str(exc)}
    return {"status": "applied", "considered": len(found), "changed": changed, "model": model}
