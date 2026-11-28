"""Reading a location question: whose, and which detail.

Both routes to the location tool (the fast path in main.py and the
orchestrator's LocationRequest) used to carry their own copy of these
keyword rules; this is the one copy.
"""
from __future__ import annotations

import re

_WHO = re.compile(
    r"(?:where is|where's|how fast is|speed of|when will|when's|how far is|how long (?:until|till|before)|eta (?:for|of))"
    r"\s+([A-Za-z]+)",
    re.IGNORECASE,
)
_ETA_WORDS = ("when will", "when's", "how long until", "how long till", "how long before", "how far",
              "eta", "get home", "be home", "arrive", "get there", "be there")
# "how long will it take me to get to ..." - the shape a person actually uses,
# and the one that used to be read as "how long has he been still".
_TAKE_TIME = re.compile(r"\bhow long (?:will|would|does|do|did)?\s*(?:it\s+)?take\b", re.IGNORECASE)
# "from A to B": both ends of a journey, so no person is needed at all.
_ROUTE = re.compile(
    r"\bfrom\s+(?:the\s+)?([a-z0-9][a-z0-9'.,&# -]{0,60}?)\s+(?:to|into)\s+(?:the\s+)?"
    r"([a-z0-9][a-z0-9'.,&# -]{0,60}?)\s*\??$",
    re.IGNORECASE,
)
# A place: a zone or place name, or a street address ("1234 Main St, Mesa")
_TO = re.compile(r"\b(?:get|be|arrive|reach|from|to|at)\s+(?:at\s+|to\s+)?(?:the\s+)?([a-z0-9][a-z0-9'.,&# -]{1,80}?)\s*\??$",
                 re.IGNORECASE)
_NOT_PLACES = {"there", "here", "it", "me"}
_CLOSEST = re.compile(r"\bwho(?:'s| is)\s+(?:the\s+)?(?:closest|nearest)\s+(?:to\s+)?(?:the\s+)?([a-z][a-z' ]{1,30}?)\s*\??$",
                      re.IGNORECASE)
_SELF = {"i", "me", "my", "myself", "we", "us"}
# "take me to get to the auction": the regex starts at the first "to" and
# swallows the verb that follows it, so the captured place needs its leading
# verb taken back off.
_PLACE_VERB = re.compile(
    r"^(?:get|go|drive|walk|travel|head|ride|take|be|arrive|reach)\s+(?:to\s+|at\s+|into\s+)?",
    re.IGNORECASE,
)


def _clean_place(raw: str) -> str:
    """A captured place with any leading travel verb removed."""
    text = (raw or "").strip()
    while True:
        stripped = _PLACE_VERB.sub("", text, count=1).strip()
        if stripped == text:
            return text
        text = stripped
_WALKING = re.compile(r"\b(?:walking|walk|on foot|by foot)\b", re.IGNORECASE)
_DRIVING = re.compile(r"\b(?:driving|drive|by car|in the car|by road)\b", re.IGNORECASE)


def _mode_of(query: str) -> str | None:
    """How they said they are travelling, or None when they did not say."""
    if _WALKING.search(query):
        return "walking"
    if _DRIVING.search(query):
        return "driving"
    return None


def parse_location_query(query: str, default_user: str | None) -> dict:
    """{"user", "detail", "to", "origin", "mode"} for a location question.

    detail is None for a plain "where is"; to is set only for an ETA,
    defaulting to "home" when they named no destination. origin is the
    other end of a journey when they gave one, so a place-to-place ETA
    ("from work to the school") needs no person; mode is "walking" or
    "driving" when they said which.
    """
    q = (query or "").lower()
    m = _WHO.search(query or "")
    user = m.group(1).strip() if m else default_user
    if user and user.lower() in _SELF:
        user = default_user
    near = _CLOSEST.search(query or "")
    if near:
        return {"user": None, "detail": "closest", "to": near.group(1).strip().lower(),
                "origin": None, "mode": None}

    detail = None
    to = None
    origin = None
    route = _ROUTE.search(query or "")
    if route:
        # Both ends named, so this is a journey whatever the surrounding words.
        origin = _clean_place(route.group(1)).lower()
        to = _clean_place(route.group(2)).lower()
        detail = "eta"
    elif any(k in q for k in _ETA_WORDS) or _TAKE_TIME.search(query or ""):
        detail = "eta"
        place = _TO.search(query or "")
        named = _clean_place(place.group(1)).lower() if place else ""
        to = named if named and named not in _NOT_PLACES else "home"
    elif any(k in q for k in ("speed", "fast", "mph", "driving")):
        detail = "speed"
    elif any(k in q for k in ("still", "dwell", "stationary", "how long")):
        detail = "dwell"
    elif any(k in q for k in ("frequent", "often", "most visited", "places")):
        detail = "frequented"
    elif any(k in q for k in ("cost", "mpg", "vehicle", "fuel", "gas")):
        detail = "cost"
    return {"user": user, "detail": detail, "to": to, "origin": origin,
            "mode": _mode_of(query) if detail == "eta" else None}
