"""The household's own model reads an app's Play listing and reviews.

Telemetry asks the local model (``llm_local_url`` with the
``guardian_monitor_model`` setting, the same one content monitoring uses; the
listing never leaves the house again) one question per app: what should a
parent of a child know about it? The answer must be this JSON::

    {"summary": "two or three plain sentences",
     "concerns": [{"category": "<one of app_vetting.CONCERNS>",
                   "severity": "medium" | "high" | "severe",
                   "why": "one sentence",
                   "evidence": ["a short quote copied from a review"]}]}

``parse`` rejects anything else with a ValueError naming what was wrong, and
the caller keeps the review as failed with that reason: a model's malformed
answer is never patched up into a verdict. A quote that does not appear in
the reviews it was given is not evidence and is dropped (counted in
``dropped_quotes``), so a parent is never shown words nobody wrote.

Pure: building the request and reading the answer are here; telemetry makes
the call (``services/telemetry/app_catalog.py``).
"""

from __future__ import annotations

import json
import re
from typing import Any

from services.common.app_vetting import CONCERNS, LEVELS

MAX_SUMMARY = 600
MAX_WHY = 240
MAX_CONCERNS = 8
MAX_EVIDENCE = 3
MAX_QUOTE = 240
#: Reviews sent to the model: the page Play shows first, trimmed.
PROMPT_REVIEWS = 20

SYSTEM_PROMPT = (
    "You help Christian parents decide whether an Android app is right for their child. You are given the app's "
    "public Google Play listing (its content rating, rating descriptors, interactive elements, ads, in-app "
    "purchases, data-safety summary and description) and recent user reviews. Say what a parent should know. "
    "Raise a concern only when the listing or the reviews support it; never invent one. Categories: "
    + ", ".join(f"{key} ({label})" for key, label in CONCERNS.items())
    + ". Severity uses this scale: severe = a real danger to a child (strangers seeking out children, sexual "
    "content reaching children, self-harm content); high = clearly not for most children (open chat with "
    "strangers, gambling or loot boxes, predatory purchases, graphic violence, drugs); medium = worth a "
    "parent's look (heavy ads, broad data collection, scary content, many complaints about bugs). Evidence "
    "must be short quotes copied word for word from the reviews given, or an empty list. Answer with JSON "
    'only: {"summary": "two or three plain sentences", "concerns": [{"category": "...", "severity": '
    '"medium" or "high" or "severe", "why": "one sentence", "evidence": ["quote"]}]}. '
    "An app with nothing to worry about has an empty concerns list."
)


def messages(listing: dict[str, Any]) -> list[dict[str, str]]:
    """The chat request for one listing (``play_listing.parse`` output)."""
    safety = listing.get("data_safety") or {}
    facts = [
        f"App: {listing.get('title')} by {listing.get('developer') or 'unknown developer'} ({listing.get('genre') or 'no genre'})",
        f"Content rating: {listing.get('content_rating') or 'not rated'}",
        f"Rating descriptors: {', '.join(listing.get('content_descriptors') or []) or 'none'}",
        f"Interactive elements: {', '.join(listing.get('interactive_elements') or []) or 'none'}",
        f"Contains ads: {'yes' if listing.get('contains_ads') else 'no'}",
        f"In-app purchases: {listing.get('in_app_price_range') or ('yes' if listing.get('in_app_purchases') else 'no')}",
        "Data safety: " + ("; ".join(
            line["heading"] + (f" ({line['detail']})" if line.get("detail") else "") for line in safety.get("lines") or []
        ) or "not given"),
        f"Stars: {listing.get('score')} from {listing.get('ratings_count')} ratings; installs {listing.get('installs')}",
    ]
    reviews = [
        f"[{i + 1}] {r.get('stars')} stars ({r.get('at') or 'date unknown'}): {r.get('text')}"
        for i, r in enumerate((listing.get("reviews") or [])[:PROMPT_REVIEWS])
    ]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "\n".join(facts)
            + f"\n\nDescription:\n\"\"\"\n{listing.get('description') or ''}\n\"\"\""
            + "\n\nReviews:\n" + ("\n".join(reviews) if reviews else "(none)"),
        },
    ]


#: Curly quotes read as straight ones when a quote is matched to a review.
_QUOTES = {0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"'}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_QUOTES)).strip().lower()


def parse(content: str, listing: dict[str, Any]) -> dict[str, Any]:
    """The model's answer as ``{"summary", "concerns", "dropped_quotes"}``.

    Raises ValueError for anything that is not the asked-for JSON: no object,
    unreadable JSON, a missing summary, an unknown category or severity, or a
    field of the wrong type.
    """
    raw = (content or "").strip()
    found = re.search(r"\{.*\}", raw, re.DOTALL)
    if not found:
        raise ValueError("the model did not answer with JSON")
    try:
        data = json.loads(found.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError("the model's JSON could not be read") from exc
    if not isinstance(data, dict):
        raise ValueError("the model's answer was not an object")
    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("the model's answer had no summary")
    concerns = data.get("concerns")
    if not isinstance(concerns, list):
        raise ValueError("the model's concerns were not a list")
    if len(concerns) > MAX_CONCERNS:
        raise ValueError(f"the model raised more than {MAX_CONCERNS} concerns")
    sources = [_norm(r.get("text") or "") for r in listing.get("reviews") or []]
    dropped = 0
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in concerns:
        if not isinstance(item, dict):
            raise ValueError("a concern was not an object")
        category = item.get("category")
        if category not in CONCERNS:
            raise ValueError(f"unknown concern category: {category!r}")
        severity = item.get("severity")
        if severity not in LEVELS:
            raise ValueError(f"unknown severity: {severity!r}")
        why = item.get("why")
        if not isinstance(why, str) or not why.strip():
            raise ValueError(f"the {category} concern had no reason")
        evidence = item.get("evidence", [])
        if not isinstance(evidence, list) or not all(isinstance(q, str) for q in evidence):
            raise ValueError(f"the {category} concern's evidence was not a list of quotes")
        quotes = []
        for quote in evidence:
            clean = quote.strip().strip('"').strip()
            if clean and any(_norm(clean) in source for source in sources):
                quotes.append(clean[:MAX_QUOTE])
            elif clean:
                dropped += 1
        if category in seen:
            # One entry per category: keep the more serious.
            prior = next(c for c in out if c["category"] == category)
            if LEVELS.index(severity) > LEVELS.index(prior["severity"]):
                prior.update(severity=severity, why=why.strip()[:MAX_WHY])
            prior["evidence"] = (prior["evidence"] + quotes)[:MAX_EVIDENCE]
            continue
        seen.add(category)
        out.append({
            "category": category,
            "severity": severity,
            "why": why.strip()[:MAX_WHY],
            "evidence": quotes[:MAX_EVIDENCE],
        })
    return {"summary": summary.strip()[:MAX_SUMMARY], "concerns": out, "dropped_quotes": dropped}
