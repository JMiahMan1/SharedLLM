"""A deck from Markdown, as one self-contained HTML file.

Presentations in a workspace should not need a build step, a CDN or a network at
show time: ``slides/index.html`` opens from the workspace, on a phone, offline.
The Markdown is deliberately small -- ``---`` separates slides, ``#`` is the slide
title, ``-`` is a bullet, ``![alt](path)`` places a picture, ``>`` is a quote and
``::`` adds speaker notes -- because it is written by a person or a model that is
not going to get reveal.js syntax right.
"""
from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from services.gateway.studio import templates

SLIDE_RE = re.compile(r"^\s*---\s*$", re.MULTILINE)
TITLE_RE = re.compile(r"^#\s+(?P<title>.+)$")
IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<path>[^)\s]+)(?:\s+\"(?P<caption>[^\"]*)\")?\)")
NOTE_RE = re.compile(r"^::\s*(?P<note>.+)$")


@dataclass
class Slide:
    title: str = ""
    bullets: list[str] = field(default_factory=list)
    image: str = ""
    caption: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_empty(self) -> bool:
        return not (self.title or self.bullets or self.image)


def parse(markdown: str) -> list[Slide]:
    """Split a Markdown deck into slides."""
    slides: list[Slide] = []
    for block in SLIDE_RE.split(markdown or ""):
        slide = Slide()
        for raw in block.splitlines():
            line = raw.strip()
            if not line:
                continue
            title = TITLE_RE.match(line)
            if title and not slide.title:
                slide.title = title.group("title").strip()
                continue
            image = IMAGE_RE.search(line)
            if image and not slide.image:
                slide.image = image.group("path")
                slide.caption = (image.group("caption") or image.group("alt") or "").strip()
                continue
            note = NOTE_RE.match(line)
            if note:
                slide.note = (slide.note + " " + note.group("note")).strip()
                continue
            if line.startswith(("- ", "* ")):
                slide.bullets.append(line[2:].strip())
                continue
            if line.startswith(">"):
                slide.bullets.append(line.lstrip("> ").strip())
                continue
            # Anything else is a sentence the author wanted on the slide.
            slide.bullets.append(line)
        if not slide.is_empty:
            slides.append(slide)
    return slides


def _slide_html(slide: Slide, *, index: int) -> str:
    parts = [f'<section class="slide" data-index="{index}">']
    if slide.title:
        parts.append(f"<h1>{html.escape(slide.title)}</h1>")
    if slide.image:
        caption = f"<figcaption>{html.escape(slide.caption)}</figcaption>" if slide.caption else ""
        parts.append(
            f'<figure><img src="{html.escape(slide.image)}" alt="{html.escape(slide.caption)}">{caption}</figure>'
        )
    if slide.bullets:
        items = "".join(f"<li>{html.escape(item)}</li>" for item in slide.bullets)
        parts.append(f"<ul>{items}</ul>")
    if slide.note:
        parts.append(f'<aside class="notes">{html.escape(slide.note)}</aside>')
    parts.append("</section>")
    return "".join(parts)


def render(slides: list[Slide], title: str, *, subtitle: str = "", footer: str = "") -> str:
    """One HTML document: every slide, keyboard and swipe navigation, offline."""
    body = "\n".join(_slide_html(slide, index=index) for index, slide in enumerate(slides))
    heading = html.escape(title or "Presentation")
    return templates.render(
        templates.DECK_HTML,
        title=heading,
        subtitle=html.escape(subtitle),
        footer=html.escape(footer) if footer else heading,
        slides=body,
        count=str(len(slides)),
        css=templates.DECK_CSS,
        js=templates.DECK_JS,
    )
