"""Ask about a passage, with the passage and its study notes in front of the model.

The reader asks a question about what is on the screen. The prompt carries the
verses, the rest of their chapter and the study notes stored for them, and the
model starts there and then brings in what a study Bible would: related
passages, setting, word meanings and how the passage has been understood.
Answering only from the verses and notes gave thin answers to most real
questions ("why", "what did this mean to them", "how does this fit").

Quotes stay honest: only the text given is quoted; other passages are named by
reference (and become chips that open the real text), never quoted from memory.

This module deliberately holds no scripture and no model name: the text arrives
from the corpus, and the model is named by the gateway's own alias so the
provider can change without a bible deploy.
"""

from __future__ import annotations

import json
import logging
import re

import aiohttp

from .refs import ReferenceError, parse_one

log = logging.getLogger("bible.study_ask")

#: A cold model load plus a long passage can exceed two minutes, and the
#: reader is waiting on an answer rather than a timeout.
STUDY_ASK_TIMEOUT = 300.0

#: Refusal limits. They are refusals rather than truncations: an answer built
#: from silently shortened Scripture would read as if it had seen all of it.
MAX_QUESTION_CHARACTERS = 2_000
MAX_VERSES = 150
MAX_NOTES = 40
MAX_NOTE_CHARACTERS = 1_200
#: Verses of the surrounding chapter sent for context, nearest first.
MAX_CONTEXT_VERSES = 80

SYSTEM_PROMPT = (
    "You are a knowledgeable, careful Bible study helper for a family. Start "
    "from the passage, the rest of its chapter and the study notes given to "
    "you, then bring in what a good study Bible or teacher would: related "
    "Scripture from the whole Bible (the New Testament rests on the Old: show "
    "the quotations, promises and patterns it draws on), the historical and "
    "cultural setting, what the original words mean, and how the church has "
    "historically understood it. Hold to traditional, historic Christian "
    "teaching. Use the related Scripture, family library and web results you "
    "are given where they bear on the question, and say where a point comes "
    "from (a passage, a book by its title and author, a site). Make clear what "
    "the passage itself says and what is background. Quote only the text "
    "given to you; name any other passage by its reference instead of quoting "
    "it from memory. Do not invent verse text, references, quotes or sources. "
    "Where Christians "
    "disagree or something is uncertain, say so and give the main views. "
    "Answer at the length the question needs. Write for someone reading the "
    "chapter right now."
)


#: How the answer is pitched. The passage and the rules do not change with the
#: reader's age; only the words do.
DEPTHS = {
    "child": (
        "The reader is a child of about 6 to 10. Use short sentences and everyday "
        "words, explain any hard word, and keep it to three or four sentences."
    ),
    "teen": (
        "The reader is a teenager. Be direct and concrete, connect it to everyday "
        "life where the passage does, and keep it focused: a few short paragraphs at most."
    ),
    "adult": (
        "The reader is an adult. When the question asks for explanation, give a "
        "full answer: the point, the context that bears on it and the related "
        "passages, in a few paragraphs or a short list. Do not pad a simple question."
    ),
}

_REF_CANDIDATE = re.compile(
    r"\b((?:[1-3]\s?)?[A-Z][a-z]+(?:\s(?:of\s)?[A-Z][a-z]+)?)\.?\s(\d{1,3})(?::(\d{1,3})(?:\s?[-\u2013]\s?(\d{1,3}))?)?"
)
MAX_REFERENCES = 8


def system_prompt(depth: str = "adult") -> str:
    if depth not in DEPTHS:
        raise StudyAskError(f"Unknown depth {depth!r}; choose child, teen or adult.")
    guidance = DEPTHS[depth]
    return f"{SYSTEM_PROMPT} {guidance}".strip()


def find_references(answer: str) -> list[str]:
    """Scripture references the answer names, each confirmed by the parser.

    Only references the reference parser accepts are returned, so a chip in the
    reader always opens a real passage; "Chapter 3" or a stray capitalised word
    followed by a number is dropped rather than offered.
    """
    out: list[str] = []
    for match in _REF_CANDIDATE.finditer(answer or ""):
        book, chapter, verse, end = match.groups()
        tail = f" {chapter}" + (f":{verse}" if verse else "") + (f"-{end}" if end else "")
        # The pattern can take the word before a book as part of its name
        # ("Compare John 3:16"), so try the longest name first, then shorter.
        words = book.split()
        display = None
        for i in range(len(words)):
            try:
                display = parse_one(" ".join(words[i:]) + tail).display()
                break
            except ReferenceError:
                continue
        if display is None:
            continue
        if display not in out:
            out.append(display)
        if len(out) >= MAX_REFERENCES:
            break
    return out


class StudyAskError(ValueError):
    """The request itself cannot be answered (the reader's side)."""


class StudyAskUnavailable(RuntimeError):
    """The model could not be reached (the operator's side)."""


def _verse_lines(verses: list[dict]) -> list[str]:
    lines: list[str] = []
    for verse in verses:
        text = str(verse.get("text") or "").strip()
        if not text:
            continue
        reference = str(verse.get("reference") or "").strip()
        lines.append(f"{reference} {text}" if reference else text)
    return lines


def _note_lines(notes: list[dict]) -> list[str]:
    lines: list[str] = []
    for note in notes:
        body = str(note.get("body") or "").strip()
        if not body:
            continue
        if len(body) > MAX_NOTE_CHARACTERS:
            body = body[:MAX_NOTE_CHARACTERS].rstrip() + " ..."
        reference = str(note.get("reference") or "").strip()
        kind = str(note.get("kind") or "note").strip()
        label = f"{reference} ({kind})" if reference else kind
        lines.append(f"{label}: {body}")
    return lines


def _source_lines(sources: dict) -> list[str]:
    """Related Scripture, library passages and web results, each labelled by where it came from."""
    lines: list[str] = []
    related = [r for r in sources.get("related") or [] if r.get("verses")]
    if related:
        lines += ["", "Related Scripture from elsewhere in the Bible (this translation's text):"]
        for group in related:
            lines += [f"{group['reference']}:", *_verse_lines(group["verses"])]
    library = sources.get("library") or []
    if library:
        lines += ["", "From the family library (use only what bears on the question; name the book and author):"]
        for item in library:
            where = item["title"] + (f" by {item['author']}" if item.get("author") else "")
            where += f", {item['chapter']}" if item.get("chapter") else ""
            lines.append(f"- {where}: {item['text']}")
    web = sources.get("web") or []
    if web:
        lines += ["", "From a web search on how Christians understand it (snippets; name the site when used):"]
        lines += [f"- {w['title']} ({w['url']}): {w['snippet']}" for w in web]
    return lines


def build_prompt(
    *,
    reference: str,
    verses: list[dict],
    notes: list[dict],
    question: str,
    context: list[dict] | None = None,
    sources: dict | None = None,
) -> str:
    """Assemble the question with the passage and its notes in front of it.

    Raises StudyAskError for an empty question, an empty passage, or a request
    that would need more Scripture than the answer is worth -- each message
    naming the number so the reader can shorten what they asked for.
    """
    cleaned = str(question or "").strip()
    if not cleaned:
        raise StudyAskError("Ask a question about this passage.")
    if len(cleaned) > MAX_QUESTION_CHARACTERS:
        raise StudyAskError(
            f"That question is {len(cleaned)} characters; the limit is "
            f"{MAX_QUESTION_CHARACTERS}. Ask a shorter one."
        )
    if len(verses) > MAX_VERSES:
        raise StudyAskError(
            f"That passage is {len(verses)} verses; the limit is {MAX_VERSES}. "
            "Select a shorter passage."
        )

    body = _verse_lines(verses)
    if not body:
        raise StudyAskError("There is no text in this passage to ask about.")

    note_lines = _note_lines(notes[:MAX_NOTES])

    parts = [f"Passage: {reference}", "", "Scripture:", *body]
    around = _verse_lines((context or [])[:MAX_CONTEXT_VERSES])
    if around:
        parts += ["", "The rest of the chapter, for context:", *around]
    if note_lines:
        parts += ["", "Study notes for this passage:", *note_lines]
    parts += _source_lines(sources or {})
    parts += [
        "",
        "Question:",
        cleaned,
        "",
        f"Answer the question about {reference}. Start from the passage, then use "
        "the chapter, the notes, the related Scripture and sources above and your "
        "wider knowledge of Scripture and its background, holding to historic "
        "Christian teaching. Say plainly where something is uncertain.",
    ]
    return "\n".join(parts)


def _extract_answer(data: object) -> str:
    """Read the answer out of an OpenAI-shaped or gateway-native reply."""
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()
    content = data.get("response") or data.get("answer") or data.get("message")
    if isinstance(content, dict):
        content = content.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    return ""


def _request(
    *,
    prompt: str,
    user: str,
    gateway_url: str,
    internal_secret: str,
    depth: str,
    stream: bool,
    system: str | None = None,
) -> tuple[str, dict, dict]:
    """The gateway call for a study question: URL, body and headers.

    ``system`` replaces the study-helper persona for callers that ask the same
    grounded question in a different shape (the Verse of the Day context note).
    The passage, notes and gateway plumbing stay identical.
    """
    if not gateway_url:
        raise StudyAskUnavailable(
            "The language model gateway is not configured, so study help is "
            "unavailable. An administrator needs to set GATEWAY_INTERNAL_URL "
            "(or gateway_internal_url)."
        )
    body = {
        "model": "assistant",
        "messages": [
            {"role": "system", "content": system or system_prompt(depth)},
            {"role": "user", "content": prompt},
        ],
        "grounded": True,
        # Reasoning models blend their thinking into `content` unless the
        # caller opts out, and a thinking trace is not an answer to show a
        # reader. Same opt-out the Wander insight card uses.
        "think": False,
        "enable_thinking": False,
        "stream": stream,
    }
    headers = {"X-Internal-Secret": internal_secret} if internal_secret else {}
    return f"{gateway_url.rstrip('/')}/v1/chat/completions", body, headers


async def _refusal(resp) -> StudyAskUnavailable:
    """The gateway's own reason for an HTTP error, as a named failure."""
    detail = ""
    try:
        payload = await resp.json()
        if isinstance(payload, dict):
            detail = str(payload.get("detail") or payload.get("message") or "").strip()
    except Exception:
        detail = ""
    return StudyAskUnavailable(
        f"The model did not answer (HTTP {resp.status})" + (f": {detail}" if detail else ".")
    )


async def ask(
    *,
    prompt: str,
    user: str,
    gateway_url: str,
    internal_secret: str,
    timeout: float = STUDY_ASK_TIMEOUT,
    depth: str = "adult",
    system: str | None = None,
) -> str:
    """Send the assembled question to the gateway and return the answer.

    Every way this can fail is raised as StudyAskUnavailable with the reason
    attached, so the reader is told what went wrong rather than shown an empty
    panel. Failures are never turned into a placeholder answer.
    """
    url, body, headers = _request(
        prompt=prompt,
        user=user,
        gateway_url=gateway_url,
        internal_secret=internal_secret,
        depth=depth,
        stream=False,
        system=system,
    )
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session, session.post(url, json=body, headers=headers) as resp:
            if resp.status >= 400:
                raise await _refusal(resp)
            try:
                data = await resp.json()
            except Exception as exc:
                raise StudyAskUnavailable(
                    "The model did not answer: the reply was not JSON."
                ) from exc
    except aiohttp.ClientError as exc:
        raise StudyAskUnavailable(
            f"Study help could not reach the language model: {exc}"
        ) from exc

    answer = _extract_answer(data)
    if not answer:
        raise StudyAskUnavailable("The model returned an empty answer.")
    return answer


async def ask_stream(
    *,
    prompt: str,
    user: str,
    gateway_url: str,
    internal_secret: str,
    timeout: float = STUDY_ASK_TIMEOUT,
    depth: str = "adult",
    system: str | None = None,
):
    """Yield the answer as it is written (OpenAI-style server-sent events).

    The same failures as ``ask`` are raised as StudyAskUnavailable, including
    a stream that ends without a word of answer.
    """
    url, body, headers = _request(
        prompt=prompt,
        user=user,
        gateway_url=gateway_url,
        internal_secret=internal_secret,
        depth=depth,
        stream=True,
        system=system,
    )
    wrote = False
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session, session.post(url, json=body, headers=headers) as resp:
            if resp.status >= 400:
                raise await _refusal(resp)
            async for raw in resp.content:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0].get("delta") or {}
                except (ValueError, KeyError, IndexError, TypeError):
                    continue
                text = delta.get("content") or ""
                if text:
                    wrote = True
                    yield text
    except aiohttp.ClientError as exc:
        raise StudyAskUnavailable(f"Study help could not reach the language model: {exc}") from exc
    if not wrote:
        raise StudyAskUnavailable("The model returned an empty answer.")
