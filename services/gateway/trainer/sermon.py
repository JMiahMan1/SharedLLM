"""Written works: a sermon, grounded on scripture, inside a small context window.

A small model cannot write a whole sermon in one prompt without either running
out of context or repeating itself. So the work is split the way a preacher
actually works: first the outline (title, big idea, points, application,
prayer), then each point expanded on its own, each call small. Scripture is read
from the family's Bible service first, so the sermon is built on the text rather
than on the model's memory of it -- and the file says plainly when the Bible
service was unavailable.

The finished sermon is written into the workspace through the Workspace API, and
:func:`illustrate` edits an image in that same workspace with the image tool.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import aiohttp

from services.gateway import workspace_api as ws
from services.gateway.config import EXECUTION_SVC, INTERNAL_SECRET

log = logging.getLogger("gateway.trainer.sermon")

#: Scripture is quoted into the prompt, so a long passage is clipped rather than
#: silently dropped: the note in the file tells the preacher what was used.
MAX_SCRIPTURE_CHARS = 2600

#: Each generated piece is one small call: the outline, then one per point.
OUTLINE_MAX_TOKENS = 520
POINT_MAX_TOKENS = 380

_ACCEPTED_SUFFIXES = (".md", ".txt")

#: ``Title:``, ``**Big idea:**``, ``1. Point`` -- the labels the outline prompt asks for.
_LABEL = re.compile(
    r"^\s*(?:[-*#]+\s*)?\*{0,2}\s*(title|big idea|point\s*\d+|application|prayer)\s*\*{0,2}"
    r"\s*[:.\-]\s*\*{0,2}\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


class SermonError(RuntimeError):
    """The sermon could not be drafted."""


@dataclass
class Sermon:
    """A drafted sermon, plus the file it was saved to."""

    title: str
    passage: str
    translation: str
    theme: str = ""
    series: str = ""
    scripture: str = ""
    big_idea: str = ""
    points: list[dict[str, str]] = field(default_factory=list)
    application: str = ""
    prayer: str = ""
    path: str = ""
    markdown: str = ""
    model: str = ""
    words: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: A completion seam: the prompt plus the token ceiling for it.
AskFn = Callable[..., Awaitable[str]]


def slugify(text: str, *, fallback: str = "sermon") -> str:
    """A file-safe slug: ``"The God Who Works" -> "the-god-who-works"``."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:60].strip("-") or fallback


def parse_outline(text: str) -> dict[str, str]:
    """Split a model's outline into its labelled parts.

    Small models drift: some answer with ``**Title:**``, some with ``1. Point``,
    some with prose. The parser takes what it recognises and leaves the rest as
    ``body``, which :func:`render_markdown` still includes rather than losing it.
    """
    parts: dict[str, str] = {}
    for label, value in _LABEL.findall(text or ""):
        key = re.sub(r"\s+", " ", label.strip().lower())
        cleaned = value.strip().strip("*").strip()
        if not cleaned:
            continue
        parts.setdefault(key, cleaned)
    leftover = _LABEL.sub("", text or "").strip()
    if leftover:
        parts["body"] = leftover
    return parts


def outline_points(parts: dict[str, str]) -> list[tuple[str, str]]:
    """The numbered points found in an outline, in order."""
    found: list[tuple[int, str, str]] = []
    for key, value in parts.items():
        match = re.fullmatch(r"point\s*(\d+)", key)
        if match:
            found.append((int(match.group(1)), value, value))
    return [(value, value) for _, value, value in sorted(found)]


def build_outline_prompt(
    *,
    passage: str,
    translation: str,
    theme: str,
    audience: str,
    tone: str,
    points: int,
    series: str,
    scripture: str,
) -> str:
    """The first call: a labelled outline the rest of the pipeline can follow."""
    lines = [
        f"Write a sermon outline on {passage} ({translation}) for {audience}.",
        f"Tone: {tone}.",
    ]
    if theme:
        lines.append(f"Theme to press: {theme}.")
    if series:
        lines.append(f"It is part of the series \"{series}\".")
    lines += [
        "",
        "Use exactly these labels, one per line:",
        "Title: <a short title>",
        "Big idea: <one sentence>",
    ]
    for index in range(1, max(1, points) + 1):
        lines.append(f"Point {index}: <one sentence, asserted not described>")
    lines += ["Application: <one concrete thing the hearer does this week>", "Prayer: <two sentences>"]
    if scripture:
        lines += ["", "The passage reads:", scripture[:MAX_SCRIPTURE_CHARS]]
    lines += ["", "Reply with the labelled lines only. No preamble, no commentary."]
    return "\n".join(lines)


def build_point_prompt(
    *,
    index: int,
    heading: str,
    big_idea: str,
    passage: str,
    tone: str,
    scripture: str,
) -> str:
    """One expansion call: a single point, standing on the passage."""
    return "\n".join(
        [
            f"Write one paragraph (90-140 words) developing this point of a sermon on {passage}:",
            f"Point {index}: {heading}",
            "",
            f"The sermon's big idea: {big_idea or 'the good news this passage announces'}",
            f"Tone: {tone}.",
            "Open with the claim, ground it in the passage, then show what it changes for the hearer.",
            "Do not invent quotations or statistics. Plain prose only, no headings.",
            "",
            f"The passage (for reference):\n{scripture[:MAX_SCRIPTURE_CHARS]}" if scripture else "",
        ]
    ).strip()


def render_markdown(sermon: Sermon) -> str:
    """The file that lands in the workspace: readable on a phone, honest about sources."""
    parts: list[str] = [f"# {sermon.title}", ""]
    meta = [f"**Text:** {sermon.passage} ({sermon.translation})"]
    if sermon.series:
        meta.append(f"**Series:** {sermon.series}")
    if sermon.theme:
        meta.append(f"**Theme:** {sermon.theme}")
    parts += [" · ".join(meta), ""]

    if sermon.scripture:
        parts += ["> " + sermon.scripture.strip().replace("\n", "\n> "), ""]
    else:
        parts += ["_Scripture was not fetched (the Bible service was unavailable); "
                  "read the passage before preaching this._", ""]

    if sermon.big_idea:
        parts += [f"**Big idea:** {sermon.big_idea}", ""]
    for index, point in enumerate(sermon.points, start=1):
        heading = point.get("heading") or f"Point {index}"
        parts += [f"## {index}. {heading}", "", point.get("text", "").strip(), ""]
    if sermon.application:
        parts += ["## Application", "", sermon.application.strip(), ""]
    if sermon.prayer:
        parts += ["## Prayer", "", sermon.prayer.strip(), ""]

    parts += [
        "---",
        f"_Drafted by Jarvis on a local model ({sermon.model or 'unrecorded'}) on "
        f"{time.strftime('%Y-%m-%d', time.gmtime())}. Scripture from the family's Bible service; "
        "check every quotation before it is preached._",
    ]
    return "\n".join(parts).rstrip() + "\n"


async def _execution_post(endpoint: str, payload: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
    """POST to the execution service (the one seam tests replace)."""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as client, client.post(
        f"{EXECUTION_SVC}{endpoint}",
        json=payload,
        headers={"X-Internal-Secret": INTERNAL_SECRET},
    ) as resp:
        try:
            body = await resp.json(content_type=None)
        except Exception:
            body = {"status": "ERROR", "message": (await resp.text())[:500]}
        return body if isinstance(body, dict) else {"status": "ERROR", "message": str(body)}


async def fetch_scripture(passage: str, translation: str, user: str) -> str:
    """Read the passage from the family's Bible service; empty when unavailable."""
    body = await _execution_post(
        "/execute/bible",
        {
            "action": "read",
            "ref": passage,
            "version": translation or None,
            "user_context": {"user": user},
        },
        timeout=60.0,
    )
    if str(body.get("status") or "").upper() != "SUCCESS":
        log.info("[trainer.sermon] scripture unavailable for %s: %s", passage, body.get("message"))
        return ""
    return str(body.get("message") or "").strip()


async def _ask(ask: AskFn | None, prompt: str, max_tokens: int, llm_mod: Any) -> tuple[str, str]:
    """One model call; returns ``(text, model)``."""
    if ask is not None:
        return str(await ask(prompt, max_tokens=max_tokens) or ""), ""
    completion = await llm_mod.complete(prompt, max_tokens=max_tokens, context_chars=8000, timeout=300.0)
    return completion.text, completion.model


async def compose_sermon(
    workspace_id: str,
    *,
    passage: str,
    title: str = "",
    theme: str = "",
    translation: str = "kjv",
    audience: str = "a Sunday congregation",
    tone: str = "warm and expository",
    points: int = 3,
    series: str = "",
    user: str = "default",
    save: bool = True,
    ask: AskFn | None = None,
) -> Sermon:
    """Draft a sermon and (by default) save it as ``sermons/<slug>.md``."""
    from services.gateway.trainer import llm as llm_mod

    passage = (passage or "").strip()
    if not passage:
        raise SermonError("A passage is required, for example 'Romans 8:28'.")

    sermon = Sermon(title=title.strip(), passage=passage, translation=translation, theme=theme, series=series)

    try:
        sermon.scripture = await fetch_scripture(passage, translation, user)
    except Exception as exc:  # the sermon still gets written; the file says why not
        sermon.notes.append(f"scripture fetch failed: {exc}")
        log.warning("[trainer.sermon] scripture fetch failed: %s", exc)
    if not sermon.scripture:
        sermon.notes.append("written without scripture text (Bible service unavailable)")

    outline_prompt = build_outline_prompt(
        passage=passage,
        translation=translation,
        theme=theme,
        audience=audience,
        tone=tone,
        points=points,
        series=series,
        scripture=sermon.scripture,
    )
    outline_text, model = await _ask(ask, outline_prompt, OUTLINE_MAX_TOKENS, llm_mod)
    sermon.model = model
    outline = parse_outline(outline_text)

    sermon.title = sermon.title or outline.get("title", "").strip() or f"A Sermon on {passage}"
    sermon.big_idea = outline.get("big idea", "").strip()
    sermon.application = outline.get("application", "").strip()
    sermon.prayer = outline.get("prayer", "").strip()

    for index, (heading, _) in enumerate(outline_points(outline), start=1):
        prompt = build_point_prompt(
            index=index,
            heading=heading,
            big_idea=sermon.big_idea,
            passage=passage,
            tone=tone,
            scripture=sermon.scripture,
        )
        body, point_model = await _ask(ask, prompt, POINT_MAX_TOKENS, llm_mod)
        sermon.model = sermon.model or point_model
        sermon.points.append({"heading": heading, "text": body.strip()})

    if not sermon.points and outline.get("body"):
        # The model ignored the labels: keep its prose rather than lose the work.
        sermon.points.append({"heading": "Draft", "text": outline["body"]})
        sermon.notes.append("the outline came back unlabelled; its prose was kept as one section")

    sermon.markdown = render_markdown(sermon)
    sermon.words = len(sermon.markdown.split())

    if save:
        slug = slugify(sermon.title)
        sermon.path = f"sermons/{slug}.md"
        await ws.write_file(workspace_id, sermon.path, sermon.markdown, user=user)
    return sermon


async def illustrate(
    workspace_id: str,
    *,
    image_path: str,
    prompt: str,
    output_path: str | None = None,
    model: str | None = None,
    user: str = "default",
) -> dict[str, Any]:
    """Edit an image inside the workspace (an illustration, a series header)."""
    if not (image_path or "").strip() or not (prompt or "").strip():
        raise SermonError("image_path and prompt are required.")
    payload = {
        "workspace_id": workspace_id,
        "image_path": image_path,
        "prompt": prompt,
        "output_path": output_path,
        "model": model,
        "user_context": {"user": user},
    }
    body = await _execution_post("/execute/image_edit", payload, timeout=1200.0)
    detail = body.get("detail") or {}
    return {
        "status": str(body.get("status") or "ERROR"),
        "message": str(body.get("message") or ""),
        "output_path": detail.get("output_path") if isinstance(detail, dict) else None,
        "model": detail.get("model") if isinstance(detail, dict) else None,
    }
