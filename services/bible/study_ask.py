"""Ask about a passage, with the passage and its study notes in front of the model.

The reader asks a question about what is on the screen, so the answer is
grounded in that text rather than in whatever the model remembers about the
Bible. Two things are put in the prompt: the verses themselves and the study
notes already stored for them. Nothing else is fetched, so an answer can always
be traced back to something the reader can see.

A question that the passage does not answer is a real answer, and the prompt
says so plainly. Guessing is worse than saying "this passage does not say",
because the reader cannot tell the two apart afterwards.

This module deliberately holds no scripture and no model name: the text arrives
from the corpus, and the model is named by the gateway's own alias so the
provider can change without a bible deploy.
"""

from __future__ import annotations

import logging

import aiohttp

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

SYSTEM_PROMPT = (
    "You are a careful Bible study helper for a family. Answer the question "
    "from the passage and study notes given to you, and say so plainly when "
    "they do not answer it. Do not invent verse text, references or quotes. "
    "Keep the answer short -- a few sentences, or a short list when the "
    "question asks for one. Write for someone reading the chapter right now."
)


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


def build_prompt(
    *,
    reference: str,
    verses: list[dict],
    notes: list[dict],
    question: str,
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
    if note_lines:
        parts += ["", "Study notes for this passage:", *note_lines]
    parts += [
        "",
        "Question:",
        cleaned,
        "",
        "Answer from the passage above. If it does not answer the question, say so.",
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


async def ask(
    *,
    prompt: str,
    user: str,
    gateway_url: str,
    internal_secret: str,
    timeout: float = STUDY_ASK_TIMEOUT,
) -> str:
    """Send the assembled question to the gateway and return the answer.

    Every way this can fail is raised as StudyAskUnavailable with the reason
    attached, so the reader is told what went wrong rather than shown an empty
    panel. Failures are never turned into a placeholder answer.
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
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "rag_user": user,
        # Reasoning models blend their thinking into `content` unless the
        # caller opts out, and a thinking trace is not an answer to show a
        # reader. Same opt-out the Wander insight card uses.
        "think": False,
        "enable_thinking": False,
    }
    headers = {"X-Internal-Secret": internal_secret} if internal_secret else {}
    url = f"{gateway_url.rstrip('/')}/v1/chat/completions"

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.post(url, json=body, headers=headers) as resp:
                if resp.status >= 400:
                    detail = ""
                    try:
                        payload = await resp.json()
                        if isinstance(payload, dict):
                            detail = str(
                                payload.get("detail") or payload.get("message") or ""
                            ).strip()
                    except Exception:
                        detail = ""
                    raise StudyAskUnavailable(
                        f"The model did not answer (HTTP {resp.status})"
                        + (f": {detail}" if detail else ".")
                    )
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
