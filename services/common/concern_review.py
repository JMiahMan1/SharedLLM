"""Content monitoring's second pass (Phase 11, step 6): a local model reads
what the phrase list caught, in context, before a parent is paged.

The reviewed phrase list (``concern_monitor``) stays the first pass: it finds
candidates and every hit is traceable to a phrase. The household's own model
(the ``llm_local_url`` endpoint, nothing leaves the house) then answers one
question per candidate: is this really a concern for a parent, and how
serious? A "no" keeps the hit on record, marked dismissed, and pages nobody;
a "yes" may change its level. Two rules keep it safe:

- self-harm and exploitation (``ALWAYS_ON``) are never silenced by a model:
  a doubt is recorded next to the hit, and the parents are still paged;
- when the model is not configured, unreachable or answers nonsense, the
  phrase list decides exactly as before, and the reason is kept on the hit
  (``review.by == "phrases"``) and logged; nothing is ever dropped.

Pure: building the request and reading the answer are here; the call itself
is made by telemetry.
"""

from __future__ import annotations

import json
import re
from typing import Any

from services.common.concern_monitor import ALWAYS_ON, CATEGORIES, LEVELS

MAX_TEXT = 1500

SYSTEM_PROMPT = (
    "You help Christian parents look after their child's phone. A keyword filter flagged a message, "
    "song, video or website title from the child's phone. Decide whether it is really something a "
    "parent needs to know about, judging the words in context: song lyrics, game talk, jokes, "
    "quotes and news are usually not; a real threat, real distress, grooming, bullying aimed at "
    "a person, or real drug or sexual activity are. Answer with JSON only: "
    '{"concern": true or false, "level": "severe" or "high" or "medium", "reason": "one short sentence"}. '
    "severe: danger to the child or someone else now (self-harm, exploitation, threats). "
    "high: bullying, drugs, sexual content, secrecy with someone. medium: alcohol, vaping, gambling."
)


def messages(concern: dict[str, Any], text: str) -> list[dict[str, str]]:
    """The chat request for one candidate."""
    label = CATEGORIES.get(concern.get("category") or "", {}).get("label") or concern.get("label") or ""
    where = {"sms": "a text message", "calls": "a call", "media": "something played", "web": "a website"}.get(
        str(concern.get("feature") or ""), "something on the phone"
    )
    clipped = (text or "")[:MAX_TEXT]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Flagged as: {label} (keyword level {concern.get('level')}).\nFrom {where}"
            f" ({concern.get('action') or 'seen'}):\n\"\"\"\n{clipped}\n\"\"\"",
        },
    ]


def parse_verdict(content: str) -> dict[str, Any]:
    """The model's answer as {"concern": bool, "level": str|None, "reason": str}.

    Raises ValueError for anything that is not that, so the caller falls back
    to the phrase list with a reason instead of guessing.
    """
    raw = (content or "").strip()
    fenced = re.search(r"\{.*\}", raw, re.DOTALL)
    if not fenced:
        raise ValueError("the model did not answer with JSON")
    try:
        data = json.loads(fenced.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError("the model's JSON could not be read") from exc
    if not isinstance(data, dict) or not isinstance(data.get("concern"), bool):
        raise ValueError("the model's answer had no true/false concern")
    level = data.get("level")
    return {
        "concern": data["concern"],
        "level": level if level in LEVELS else None,
        "reason": str(data.get("reason") or "").strip()[:200],
    }


def apply(concern: dict[str, Any], verdict: dict[str, Any], model: str) -> dict[str, Any]:
    """The candidate after the model's verdict (a new dict)."""
    out = dict(concern)
    review = {"by": "model", "model": model, "reason": verdict.get("reason") or ""}
    if verdict["concern"]:
        review["verdict"] = "confirmed"
        review["phrase_level"] = concern.get("level")
        if verdict.get("level"):
            out["level"] = verdict["level"]
    elif concern.get("category") in ALWAYS_ON:
        # Never silenced by a model: the parents still hear, with its doubt beside it.
        review["verdict"] = "doubted"
    else:
        review["verdict"] = "dismissed"
    out["review"] = review
    return out


def phrase_only(concern: dict[str, Any], reason: str) -> dict[str, Any]:
    """The candidate as the phrase list decided it, with why the model did not weigh in."""
    return {**concern, "review": {"by": "phrases", "reason": reason}}


def dismissed(concern: dict[str, Any]) -> bool:
    return (concern.get("review") or {}).get("verdict") == "dismissed"
