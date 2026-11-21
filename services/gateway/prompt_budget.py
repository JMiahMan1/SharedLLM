"""What each request's prompt is made of, by section, so its size can be managed.

Every model call on the chat, voice and Raven paths records the characters each
part of its prompt contributed: the fixed system text, the tool guide, the
knowledge map, each pre-fetched collection, the history, the question and the
tool results that came back. One log line per request shows it; the admin
endpoint (``GET /api/admin/prompt-budget``) shows the aggregates.

Tokens are estimated at four characters each (``EST_CHARS_PER_TOKEN``): close
enough to compare sections and spot growth, and labelled as an estimate wherever
it is shown.

Kept in this process's memory, bounded (``RECENT_MAX`` requests): the gateway runs
one process, and the numbers are for looking at, not for billing.
"""
from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("gateway.prompt_budget")

EST_CHARS_PER_TOKEN = 4
RECENT_MAX = 500

#: Sections that are the same for every request on a path (the model server keeps
#: them warm), as opposed to what each request adds.
FIXED_SECTIONS = frozenset({"system", "tool_guide", "knowledge_map", "workspace"})


@dataclass
class PromptRecord:
    path: str
    sections: dict[str, int]
    at: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.sections.values())

    @property
    def per_request(self) -> int:
        return sum(v for k, v in self.sections.items() if k not in FIXED_SECTIONS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "at": self.at,
            "sections": dict(self.sections),
            "total_chars": self.total,
            "per_request_chars": self.per_request,
            "est_tokens": self.total // EST_CHARS_PER_TOKEN,
            **({"meta": self.meta} if self.meta else {}),
        }


_lock = threading.Lock()
_recent: deque[PromptRecord] = deque(maxlen=RECENT_MAX)


def record(path: str, sections: dict[str, int], **meta: Any) -> PromptRecord:
    """Record one request's prompt and log it on one line."""
    clean = {k: int(v) for k, v in sections.items() if v}
    rec = PromptRecord(path=path, sections=clean, meta={k: v for k, v in meta.items() if v not in (None, "", [])})
    with _lock:
        _recent.append(rec)
    parts = " ".join(f"{k}={v}" for k, v in sorted(clean.items()))
    log.info(
        "[prompt-budget] path=%s total=%s per_request=%s est_tokens=%s %s",
        path, rec.total, rec.per_request, rec.total // EST_CHARS_PER_TOKEN, parts,
    )
    return rec


def add_tool_result(rec: PromptRecord | None, chars: int) -> None:
    """Add a tool result's characters to a recorded request (they arrive after it is sent)."""
    if rec is None or chars <= 0:
        return
    with _lock:
        rec.sections["tool_results"] = rec.sections.get("tool_results", 0) + int(chars)


def _stats(values: list[int]) -> dict[str, float]:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return {
        "mean": round(statistics.fmean(ordered), 1),
        "p50": statistics.median(ordered),
        "p95": p95,
        "max": ordered[-1],
    }


def snapshot(recent: int = 20) -> dict[str, Any]:
    """Aggregates per path and section, plus the most recent requests."""
    with _lock:
        records = list(_recent)
    by_path: dict[str, list[PromptRecord]] = {}
    for rec in records:
        by_path.setdefault(rec.path, []).append(rec)
    paths: dict[str, Any] = {}
    for path, recs in sorted(by_path.items()):
        names = sorted({k for r in recs for k in r.sections})
        paths[path] = {
            "requests": len(recs),
            "total_chars": _stats([r.total for r in recs]),
            "per_request_chars": _stats([r.per_request for r in recs]),
            # A section absent from a request contributed 0 to it.
            "sections": {n: _stats([r.sections.get(n, 0) for r in recs]) for n in names},
        }
    return {
        "est_chars_per_token": EST_CHARS_PER_TOKEN,
        "window": len(records),
        "window_max": RECENT_MAX,
        "paths": paths,
        "recent": [r.to_dict() for r in records[-max(0, recent):]][::-1],
    }


def reset() -> None:
    with _lock:
        _recent.clear()
