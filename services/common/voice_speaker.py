"""Who said a voice command, handed from speech-to-text to the gateway.

Home Assistant turns a satellite's audio into text (through Jarvis's Wyoming
proxy, ``services/execution/wyoming_stt.py``) and then sends that text to the
gateway as a chat request. Neither step names the satellite or the speaker, so
the proxy records the speaker it recognised under the transcript itself, and
the gateway looks the request's text up. The record lives a few seconds:
long enough for Home Assistant's next call, too short to match a later,
unrelated request that happens to say the same words.
"""
import hashlib
import json
import re
import time

#: Home Assistant asks for the reply right after the transcript; anything
#: later than this is a different request.
TTL_SECONDS = 20

_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s']")


def normalize(text: str) -> str:
    """The words, as both sides see them: case, punctuation and spacing differ."""
    return _SPACE.sub(" ", _PUNCT.sub(" ", (text or "").lower())).strip()


def key(text: str) -> str | None:
    """The Redis key for who said ``text``; None when there are no words."""
    words = normalize(text)
    return "voice:speaker:" + hashlib.sha256(words.encode()).hexdigest() if words else None


def encode(username: str, score: float | None) -> str:
    return json.dumps({"username": username, "score": score, "at": time.time()})


def decode(raw: str | bytes | bytearray | None) -> str | None:
    """The recognised username in a stored record, if it is one."""
    if not isinstance(raw, (str, bytes, bytearray)):
        return None
    try:
        found = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        return None
    name = found.get("username") if isinstance(found, dict) else None
    return name if isinstance(name, str) and name else None
