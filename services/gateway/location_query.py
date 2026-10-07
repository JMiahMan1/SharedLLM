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
_TO = re.compile(r"\b(?:get|be|arrive|reach|from|to|at)\s+(?:at\s+|to\s+)?(?:the\s+)?([a-z][a-z' ]{1,30}?)\s*\??$",
                 re.IGNORECASE)
_NOT_PLACES = {"there", "here", "it", "me"}
_SELF = {"i", "me", "my", "myself", "we", "us"}


def parse_location_query(query: str, default_user: str | None) -> dict:
    """{"user", "detail", "to"} for a location question. detail is None for
    a plain "where is"; to is set only for an ETA, defaulting to "home"."""
    q = (query or "").lower()
    m = _WHO.search(query or "")
    user = m.group(1).strip() if m else default_user
    if user and user.lower() in _SELF:
        user = default_user
    detail = None
    to = None
    if any(k in q for k in _ETA_WORDS):
        detail = "eta"
        place = _TO.search(query or "")
        to = place.group(1).strip().lower() if place and place.group(1).strip().lower() not in _NOT_PLACES else "home"
    elif any(k in q for k in ("speed", "fast", "mph", "driving")):
        detail = "speed"
    elif any(k in q for k in ("still", "dwell", "stationary", "how long")):
        detail = "dwell"
    elif any(k in q for k in ("frequent", "often", "most visited", "places")):
        detail = "frequented"
    elif any(k in q for k in ("cost", "mpg", "vehicle", "fuel", "gas")):
        detail = "cost"
    return {"user": user, "detail": detail, "to": to}
