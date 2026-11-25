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


def encode(username: str | None, score: float | None, verdict: dict | None = None) -> str:
    """The record for one command. ``username`` is None when nobody was
    recognised: the record still carries the two best scores and the speech
    length, so the gateway can log why (never the signature)."""
    verdict = verdict or {}
    top = [
        {"username": c.get("username"), "score": c.get("score")}
        for c in (verdict.get("candidates") or [])[:2]
        if isinstance(c, dict)
    ]
    return json.dumps({
        "username": username,
        "score": score,
        "top": top,
        "threshold": verdict.get("threshold"),
        "margin": verdict.get("margin"),
        "speech_seconds": verdict.get("speech_seconds"),
        "at": time.time(),
    })


def decode_record(raw: str | bytes | bytearray | None) -> dict | None:
    """The whole stored record (who, the two best scores, speech length), or None."""
    if not isinstance(raw, (str, bytes, bytearray)) or not raw:
        return None
    try:
        found = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(found, dict):
        return None
    name = found.get("username")
    found["username"] = name if isinstance(name, str) and name else None
    if not isinstance(found.get("top"), list):
        found["top"] = []
    return found


#: A command that was not recognised but scored close to someone may become a
#: candidate sample for the person its satellite is assigned to (identity's
#: ``/api/voice/candidate``). Its signature is kept this long, under the
#: transcript, for the gateway to take; never logged, never stored elsewhere.
CANDIDATE_MIN_SCORE = 0.55
CANDIDATE_MIN_SPEECH_SECONDS = 1.5


def candidate_key(text: str) -> str | None:
    words = normalize(text)
    return "voice:candidate:" + hashlib.sha256(words.encode()).hexdigest() if words else None


def worth_offering(verdict: dict | None) -> bool:
    """Whether an unrecognised command is close enough to anyone to offer as a candidate."""
    if not verdict or verdict.get("matched") or not verdict.get("embedding"):
        return False
    speech = verdict.get("speech_seconds")
    if not isinstance(speech, (int, float)) or speech < CANDIDATE_MIN_SPEECH_SECONDS:
        return False
    best = (verdict.get("candidates") or [None])[0]
    score = best.get("score") if isinstance(best, dict) else None
    return isinstance(score, (int, float)) and score >= CANDIDATE_MIN_SCORE


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
