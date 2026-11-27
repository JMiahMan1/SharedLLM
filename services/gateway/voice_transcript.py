"""What speech to text wrote, made routable: a play or read verb glued to its object, or misheard.

Speech to text sometimes runs the verb into the next word ("PlayDork Diaries 3",
"ReDork Diaries") or hears "read chapter one of" as "reach after one of". The router then
sees no verb at all, and the model is asked about a place called "Wilder Hart". Only these
shapes are repaired, at the start of the sentence, and only for spoken requests
(``normalize`` is called with a voice context):

* "Play" or "Read" directly followed by a capital letter or a digit ("PlayDork",
  "ReadDork", "Play3"): a space is put back. A word that is really one word
  ("PlayStation", "Playlist") is left alone.
* "Re" directly followed by a capital letter ("ReDork Diaries"): "Read ".
* A "read"-like word ("re", "red", "reed", "reach" ...) and a "chapter"-like word
  ("chapter", "after", "captor") followed by a chapter number and "of" / "in" / "from":
  "Read chapter <n> of <title>". A word that only sounds like "chapter" counts only after
  a "read" word, so "After one of these, ..." is left alone.

Nothing else is changed, and every change is logged by the caller.
"""
from __future__ import annotations

import re

#: Words after which a glued "Play" is a real word, not a verb ("PlayStation", "Playmobil").
_ONE_WORD = re.compile(r"^(?:Play(?:Station|mobil|book|Doh|Date|list|ground|time|house|er)|Read(?:er|y|ing|s))\b",
                       re.IGNORECASE)
_GLUED_VERB = re.compile(r"^(?P<verb>Play|Read)(?=[A-Z0-9])")
_GLUED_RE = re.compile(r"^Re(?=[A-Z][a-z])")
_NUMBER = (r"(?:\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
           r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|first|second|third|fourth|fifth|sixth|"
           r"seventh|eighth|ninth|tenth)")
#: "Read" as speech to text has written it before a chapter.
_READ_WORD = r"(?:re|red|reed|reid|reach|reads?|ree)"
#: "Read chapter 1 of", as speech to text has written it. A word that only sounds like
#: "chapter" ("after") counts only after a "read" word; "chapter" itself is enough.
_READ_CHAPTER = re.compile(
    rf"^(?:(?P<verb>{_READ_WORD})\s*)?(?P<word>chapters?|chapter's|after|captor|chaptor)\s+"
    rf"(?P<n>{_NUMBER})\s+(?P<prep>of|in|from)\s+(?P<rest>\S.*)$",
    re.IGNORECASE,
)
_WAKE = re.compile(r"^(?P<wake>(?:(?:hey|ok|okay)\s+)?jarvis[\s,.!]*)", re.IGNORECASE)


def normalize(text: str | None) -> str:
    """``text`` with a glued or misheard play/read verb repaired; unchanged when it has none."""
    raw = text or ""
    stripped = raw.strip()
    if not stripped:
        return raw
    wake = _WAKE.match(stripped)
    head = wake.group("wake") if wake else ""
    body = stripped[len(head):]
    fixed = _fix(body)
    return raw if fixed == body else head + fixed


def _fix(body: str) -> str:
    m = _READ_CHAPTER.match(body)
    if m and (m.group("verb") or m.group("word").lower().startswith("chapter")):
        fixed = f"Read chapter {m.group('n')} {m.group('prep').lower()} {m.group('rest')}"
        return body if fixed.lower() == body.lower() else fixed
    if _ONE_WORD.match(body):
        return body
    m = _GLUED_VERB.match(body)
    if m:
        return f"{m.group('verb')} {body[len(m.group('verb')):]}"
    if _GLUED_RE.match(body):
        return f"Read {body[2:]}"
    return body
