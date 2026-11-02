"""Topics: which of the family's resources are about fasting, prayer, health...

A resource is about a topic when it is tagged with it, or kept under a
``Resources/<Topic>/`` folder:

* A Calibre book tagged ``fasting`` or ``jarvis:fasting`` (Calibre's tags reach
  every chunk as one ``"a; b"`` string, see storage ``calibre._book_metadata``).
* Any indexed file under ``.../Resources/Fasting/...`` in Nextcloud, so a PDF
  or note becomes a fasting resource by where it is put.

Topics are lower case; the ``jarvis:`` prefix is only a way to keep them apart
from a library's own tags and is dropped.
"""
from __future__ import annotations

import re
from typing import Any

_SPLIT = re.compile(r"[;,]")
_RESOURCE_DIR = re.compile(r"(?:^|/)resources/([^/]+)/", re.IGNORECASE)


def _clean(tag: str) -> str:
    tag = tag.strip().lower()
    if tag.startswith("jarvis:"):
        tag = tag[len("jarvis:"):].strip()
    return tag


def item_topics(meta: dict[str, Any]) -> set[str]:
    """Every topic one indexed item belongs to."""
    raw = meta.get("tags")
    tags: list[str] = []
    if isinstance(raw, str):
        tags = _SPLIT.split(raw)
    elif isinstance(raw, list | tuple):
        tags = [str(t) for t in raw]
    topics = {_clean(t) for t in tags if _clean(t)}
    path = str(meta.get("path") or "")
    topics |= {_clean(m) for m in _RESOURCE_DIR.findall(path)}
    return topics


def matches(meta: dict[str, Any], wanted: list[str] | None) -> bool:
    """True when ``wanted`` is empty, or the item has any of those topics."""
    if not wanted:
        return True
    return bool(item_topics(meta) & {_clean(w) for w in wanted})
