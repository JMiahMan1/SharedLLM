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
``dropped_quotes``), so a parent is never shown words nobody wrote. The
reviews a kept quote came from are returned as ``excerpts`` (stars, date and
the review's words) for the card.

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
#: Reviews sent to the model: the page Play shows first, then the newest and
#: the lowest rated (``play_listing.REVIEW_BATCHES``), each trimmed, never the
#: same review twice.
PROMPT_REVIEWS = 20
PROMPT_NEWEST = 15
PROMPT_LOWEST = 15
#: Review excerpts kept for the card: the reviews a concern quoted.
MAX_EXCERPTS = 6

SYSTEM_PROMPT = (
    "You help Christian parents decide whether an Android app is right for their child. You are given the app's "
    "public Google Play listing (its content rating, rating descriptors, interactive elements, ads, in-app "
    "purchases, data-safety summary and description) and user reviews: the ones Play shows first, the newest, "
    "and the lowest rated (one and two stars, where complaints about strangers, scams and content show up). "
    "Say what a parent should know. "
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
    sections = review_sections(listing)
    lines: list[str] = []
    n = 0
    for title, rows in sections:
        if not rows:
            continue
        lines.append(f"{title}:")
        for r in rows:
            n += 1
            lines.append(f"[{n}] {r.get('stars')} stars ({r.get('at') or 'date unknown'}): {r.get('text')}")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "\n".join(facts)
            + f"\n\nDescription:\n\"\"\"\n{listing.get('description') or ''}\n\"\"\""
            + "\n\nReviews:\n" + ("\n".join(lines) if lines else "(none)"),
        },
    ]


def _more(listing: dict[str, Any], batch: str) -> list[dict[str, Any]]:
    return list(((listing.get("more_reviews") or {}).get(batch) or {}).get("reviews") or [])


def review_sections(listing: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    """The reviews the model reads, by where they came from: Play's first page,
    the newest, and the lowest rated, each review once."""
    seen: set[str] = set()

    def fresh(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            key = _norm(r.get("text") or "")
            if key and key not in seen:
                seen.add(key)
                out.append(r)
            if len(out) >= limit:
                break
        return out

    first = fresh(listing.get("reviews") or [], PROMPT_REVIEWS)
    newest = fresh(_more(listing, "newest"), PROMPT_NEWEST)
    lowest = fresh(sorted(_more(listing, "one_star") + _more(listing, "two_star"),
                          key=lambda r: r.get("at") or "", reverse=True), PROMPT_LOWEST)
    return [("Reviews Play shows first", first), ("Newest reviews", newest),
            ("Lowest-rated reviews (1 and 2 stars)", lowest)]


def _sourced_reviews(listing: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every review the model was given, tagged with where it came from."""
    names = {"Reviews Play shows first": "relevant", "Newest reviews": "newest",
             "Lowest-rated reviews (1 and 2 stars)": "lowest"}
    return [(names[title], r) for title, rows in review_sections(listing) for r in rows]


def excerpts(concerns: list[dict[str, Any]], listing: dict[str, Any]) -> list[dict[str, Any]]:
    """The reviews a concern quoted, word for word with their stars and date,
    for the card (never the reviewer). At most ``MAX_EXCERPTS``."""
    reviews = _sourced_reviews(listing)
    out: list[dict[str, Any]] = []
    used: set[str] = set()
    for concern in concerns:
        for quote in concern.get("evidence") or []:
            needle = _norm(quote)
            for source, review in reviews:
                text = review.get("text") or ""
                if needle and needle in _norm(text) and text not in used:
                    used.add(text)
                    out.append({"category": concern["category"], "stars": review.get("stars"),
                                "at": review.get("at"), "text": text, "source": source})
                    break
            if len(out) >= MAX_EXCERPTS:
                return out
    return out


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
    sources = [_norm(r.get("text") or "") for _source, r in _sourced_reviews(listing)]
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
    return {"summary": summary.strip()[:MAX_SUMMARY], "concerns": out, "dropped_quotes": dropped,
            "excerpts": excerpts(out, listing)}
