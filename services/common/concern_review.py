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

#: How much of the message that came with a picture or voice note is kept.
#: Enough for a parent to judge the tone of the conversation around it; not so
#: much that the concern becomes a second copy of the whole chat.
MAX_MESSAGE_TEXT = 500

#: What each collector calls itself, in the words a parent reads.
SOURCE_WORDS = {"mms": "MMS", "notification": "notification", "gallery": "gallery"}


def _clean(value: Any, limit: int = 120) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit].strip()


def message_context(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """The message a scan came from, in one shape (``None`` when there is none).

    A picture or a voice note reaches the phone as a picture message, as a
    notification attachment, or as something in the gallery, and each collector
    knows slightly different things about it: who it was from, whether the child
    sent or received it, the app it came through, and the words that came with
    it. Those are the things that decide whether a scan is a bully's message or
    a meme between friends, so they are normalised here once and read from the
    same shape by the vision prompt, the stored concern and the parent's notice.

    Android sends a few spellings for the same idea (``package`` beside ``app``,
    ``sent_at`` in milliseconds); anything unrecognised is ignored rather than
    guessed at.
    """
    if not isinstance(metadata, dict) or not metadata:
        return None
    group = metadata.get("group")
    names = [name for name in (_clean(item, 60) for item in group or []) if name] if isinstance(group, list) else []
    direction = _clean(metadata.get("direction"), 12).lower()
    context = {
        "source": _clean(metadata.get("source"), 20).lower() or None,
        "direction": direction if direction in ("in", "out") else None,
        "contact": _clean(metadata.get("contact")),
        "number": _clean(metadata.get("number"), 40),
        "group": names or None,
        "message_text": _clean(metadata.get("message_text"), MAX_MESSAGE_TEXT) or None,
        "app": _clean(metadata.get("app"), 60),
        "package": _clean(metadata.get("package"), 120),
        "conversation": _clean(metadata.get("conversation"), 80),
        "sent_at": metadata.get("sent_at") if isinstance(metadata.get("sent_at"), (int, float)) else None,
    }
    return context if any(value for key, value in context.items() if key != "sent_at") else None


def message_summary(context: dict[str, Any] | None, *, with_text: bool = True) -> str:
    """One line naming who the message was from or to, and what came with it.

    ``From Alex (MMS) · Message: "..."`` reads the same in a vision prompt, a
    stored concern and a parent's push notification, so the three never
    disagree about what the child was looking at.
    """
    if not context:
        return ""
    who = context.get("contact") or context.get("number") or context.get("conversation") or "an unknown number"
    kind = context.get("app") or SOURCE_WORDS.get(str(context.get("source") or ""), "")
    where = f" ({kind})" if kind and kind.lower() not in who.lower() else ""
    line = f"Sent to {who}{where}" if context.get("direction") == "out" else f"From {who}{where}"
    if context.get("group"):
        line += f", in a group with {', '.join(context['group'][:4])}"
    text = context.get("message_text")
    if with_text and text:
        line += f' · Message: "{text}"'
    return line


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
    action = str(concern.get("action") or "")
    if action == "voice_message":
        where = "a recorded voice message in a text conversation"
    else:
        where = {"sms": "a text message", "calls": "a call", "media": "something played", "web": "a website"}.get(
            str(concern.get("feature") or ""), "something on the phone"
        )
    clipped = (text or "")[:MAX_TEXT]
    # A parent reading only the transcript has lost the person and the
    # direction; both decide whether the words were aimed at the child.
    context = concern.get("message_context")
    who = message_summary(context, with_text=False) if isinstance(context, dict) else ""
    said = (context or {}).get("message_text") if isinstance(context, dict) else None
    aside = f"\n{who}." if who else ""
    if said:
        aside += f'\nThe message with it said: "{said}"'
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Flagged as: {label} (keyword level {concern.get('level')}).\nFrom {where}"
            f" ({concern.get('action') or 'seen'}):{aside}\n\"\"\"\n{clipped}\n\"\"\"",
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


def media_messages(
    media_type: str,
    file_name: str,
    image_b64: str,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build the vision completion chat prompt for evaluating a photo/video thumbnail.

    ``metadata`` is what the phone knows about the message the picture came in:
    who sent it, whether the child sent or received it, what app, and the words
    that came with it. A picture of a bruise from a friend reads differently from
    the same picture in a bullying thread, so the context goes in the prompt.
    """
    clean_b64 = image_b64
    if "," in clean_b64:
        clean_b64 = clean_b64.split(",", 1)[1]
    clean_b64 = clean_b64.strip()
    context = message_context(metadata)
    question = f"Scanned from {media_type} file: '{file_name}'."
    if context:
        question += f"\n{message_summary(context)}."
    question += " Is there any safety concern?"
    return [
        {
            "role": "system",
            "content": (
                "You help Christian parents protect their children from harmful content on their phones. "
                "Evaluate the image for safety concerns. Check for:\n"
                "1. Sexual content, nudity, suggestive poses, underwear/lingerie, or pornography (category: 'sexual', level: 'high' or 'severe').\n"
                "2. Real weapons, gore, physical violence, or threats (category: 'violence', level: 'severe').\n"
                "3. Illegal drugs, pills, vaping, alcohol, or drug paraphernalia (category: 'drugs', level: 'high').\n"
                "4. Self-harm, cutting, or suicide (category: 'self_harm', level: 'severe').\n"
                "5. Cyberbullying, hate symbols, or harassment (category: 'bullying', level: 'high').\n"
                "If safe, return JSON: {\"concern\": false, \"reason\": \"clean\"}.\n"
                "If a concern is found, return JSON only: "
                '{"concern": true, "category": "sexual"|"violence"|"drugs"|"self_harm"|"bullying", '
                '"level": "severe"|"high"|"medium", "description": "short description of flagged content", '
                '"reason": "explanation of concern"}.'
            ),
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": question,
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{clean_b64}"},
                },
            ],
        },
    ]


def parse_media_verdict(content: str) -> dict[str, Any]:
    """Parse a vision model's response for an image/thumbnail scan."""
    raw = (content or "").strip()
    fenced = re.search(r"\{.*\}", raw, re.DOTALL)
    if not fenced:
        raise ValueError("the vision model did not answer with JSON")
    try:
        data = json.loads(fenced.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError("the vision model's JSON could not be read") from exc
    if not isinstance(data, dict) or not isinstance(data.get("concern"), bool):
        raise ValueError("the vision model answer had no true/false concern")
    cat = str(data.get("category") or "sexual").strip().lower()
    if cat not in CATEGORIES:
        cat = "sexual"
    level = data.get("level")
    if level not in LEVELS:
        level = CATEGORIES[cat]["level"]
    return {
        "concern": data["concern"],
        "category": cat,
        "level": level,
        "description": str(data.get("description") or "").strip()[:200],
        "reason": str(data.get("reason") or "").strip()[:200],
    }


#: What each category means to the second check, in plain words. Stricter than
#: the first look: a video call is mostly faces, a camera preview and buttons.
VERIFY_DEFINITIONS = {
    "sexual": "nudity, exposed private parts, sexual acts or clearly sexual posing",
    "violence": "a real weapon being used or shown as a threat, real injury, gore or physical violence",
    "drugs": "illegal drugs, drug use or drug paraphernalia in plain view",
    "self_harm": "self-harm injuries, cutting, or an act of self-harm or suicide",
    "bullying": "hate symbols, or written harassment or threats aimed at a person",
}

VERIFY_PROMPT = (
    "You are a careful second reviewer for parents. You will see one screenshot taken during a video "
    "call on a child's phone. Video call screenshots normally show faces, the child's own camera "
    "preview, call buttons, ordinary clothing, rooms and everyday objects; none of those are a "
    "concern. Answer whether the screenshot CLEARLY and UNMISTAKABLY shows: {definition}. "
    "If it is blurry, dark, partly visible, could be innocent, or you are not sure, answer false. "
    'Answer with JSON only: {{"confirmed": true or false, "reason": "one short sentence"}}.'
)


def media_verify_messages(category: str, image_b64: str) -> list[dict[str, Any]]:
    """The second, stricter vision check on one frame for one category.

    It is told only what to look for, never what the first look said, so its
    answer is an independent reading of the same picture.
    """
    definition = VERIFY_DEFINITIONS.get(category) or VERIFY_DEFINITIONS["sexual"]
    clean_b64 = image_b64.split(",", 1)[1] if "," in image_b64 else image_b64
    return [
        {"role": "system", "content": VERIFY_PROMPT.format(definition=definition)},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Does this screenshot clearly show that? JSON only."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{clean_b64.strip()}"}},
            ],
        },
    ]


def parse_media_check(content: str) -> dict[str, Any]:
    """The second check's answer as {"confirmed": bool, "reason": str}.

    Raises ValueError for anything else; the caller then treats the frame as
    not confirmed (a check that could not answer never pages a parent).
    """
    raw = (content or "").strip()
    fenced = re.search(r"\{.*\}", raw, re.DOTALL)
    if not fenced:
        raise ValueError("the second check did not answer with JSON")
    try:
        data = json.loads(fenced.group(0))
    except json.JSONDecodeError as exc:
        raise ValueError("the second check's JSON could not be read") from exc
    if not isinstance(data, dict) or not isinstance(data.get("confirmed"), bool):
        raise ValueError("the second check's answer had no true/false confirmed")
    return {"confirmed": data["confirmed"], "reason": str(data.get("reason") or "").strip()[:200]}

