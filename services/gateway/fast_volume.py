"""Spoken volume commands, answered without the model.

"Set volume to 100%" at the Office Echo went to the model on 2026-11-24, took 30 s and
changed nothing (the model's call lost its ``command`` and execution refused it three
times). A volume command has one meaning and one tool, so the FastPath handles it: the
phrase is parsed here, the target is the speaker that heard it unless another device or
room is named (the media_transport target resolution in main.py), and execution reads the
player back so the reply says the level it actually reports.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Relative steps: plain "turn it up", "a bit", "a lot".
STEP = 0.10
SMALL_STEP = 0.05
BIG_STEP = 0.25


@dataclass(frozen=True)
class VolumeAsk:
    """``kind`` is "set" (``level`` 0..1), "step" (``delta`` -1..1), "mute" or "unmute"."""

    kind: str
    level: float | None = None
    delta: float | None = None


_LEAD = r"^(?:(?:hey\s+)?jarvis[,\s]+)?(?:(?:can|could|would|will)\s+you\s+|please\s+)?"
_PLACE = r"(?:\s+(?:on|in|for)\s+(?:the\s+)?[a-z0-9' ]+?)?"
_TAIL = r"(?:\s+please)?[\s.!?]*$"
_SOUND = r"(?:the\s+)?(?:volume|music|sound|audio|speaker|tv)"
_AMOUNT = r"(?:\s+(?P<amount>a\s+(?:little\s+)?bit|a\s+little|a\s+touch|slightly|a\s+lot|a\s+bunch|way\s+more|more))?"

_NUMBER = r"(?P<n>\d{1,3}(?:\.\d+)?)\s*(?P<pct>%|\s*percent|\s*per\s*cent)?"
_SET = [
    re.compile(_LEAD + r"(?:set|change|put|turn)\s+(?:the\s+)?volume" + _PLACE + r"\s+(?:to|at)\s+" + _NUMBER + _PLACE + _TAIL),
    re.compile(_LEAD + r"(?:set|change|put|turn)\s+(?:the\s+)?" + r"(?P<dev>[a-z0-9' ]+?)\s+volume\s+(?:to|at)\s+" + _NUMBER + _TAIL),
    re.compile(_LEAD + r"volume\s+(?:to\s+|at\s+)?" + _NUMBER + _PLACE + _TAIL),
    re.compile(_LEAD + r"(?:set|turn|put)\s+(?:it|the\s+music|the\s+sound)\s+(?:to|at)\s+" + _NUMBER + _PLACE + _TAIL),
]
_NAMED_LEVELS = [
    (re.compile(_LEAD + r"(?:set\s+(?:the\s+)?volume\s+to\s+|turn\s+(?:the\s+)?volume\s+to\s+)?"
                r"(?:max(?:imum)?|full|top)\s+volume" + _PLACE + _TAIL), 1.0),
    (re.compile(_LEAD + r"(?:set|turn|put)\s+(?:the\s+)?volume\s+(?:to\s+)?(?:the\s+)?(?:max(?:imum)?|full|top)"
                + _PLACE + _TAIL), 1.0),
    (re.compile(_LEAD + r"turn\s+(?:it|" + _SOUND + r")\s+all\s+the\s+way\s+up" + _PLACE + _TAIL), 1.0),
    (re.compile(_LEAD + r"(?:set\s+(?:the\s+)?volume\s+to\s+)?half\s+volume" + _PLACE + _TAIL), 0.5),
    (re.compile(_LEAD + r"(?:set|turn|put)\s+(?:the\s+)?volume\s+(?:to\s+)?half(?:\s+way)?" + _PLACE + _TAIL), 0.5),
]
_UP_DOWN = [
    re.compile(_LEAD + r"turn\s+(?:it|" + _SOUND + r")\s+(?P<dir>up|down)" + _AMOUNT + _PLACE + _TAIL),
    re.compile(_LEAD + r"turn\s+(?P<dir>up|down)\s+(?:it|" + _SOUND + r")" + _AMOUNT + _PLACE + _TAIL),
    re.compile(_LEAD + r"(?:the\s+)?volume\s+(?P<dir>up|down)" + _AMOUNT + _PLACE + _TAIL),
    re.compile(_LEAD + r"(?:make\s+it\s+|play\s+it\s+)?"
               r"(?:(?P<amount_pre>a\s+(?:little\s+)?bit|a\s+little|a\s+lot|slightly|much)\s+)?"
               r"(?P<word>louder|quieter|softer)" + _AMOUNT + _PLACE + _TAIL),
]
_MUTE = re.compile(_LEAD + r"(?P<word>mute|unmute|un-mute)(?:\s+(?:it|" + _SOUND + r"))?" + _PLACE + _TAIL)


def _clean(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


def _amount(text: str | None) -> float:
    words = (text or "").strip()
    if not words:
        return STEP
    if words in ("a lot", "a bunch", "way more"):
        return BIG_STEP
    if words == "more":
        return STEP
    return SMALL_STEP


def clamp(level: float) -> float:
    return max(0.0, min(1.0, level))


def _level(match: re.Match) -> float:
    n = float(match.group("n"))
    if match.group("pct") or n > 10:
        return clamp(n / 100.0)
    # "Volume 7": a 0-10 scale, the way a speaker's own buttons count.
    return clamp(n / 10.0)


def parse(query: str) -> VolumeAsk | None:
    """The volume command in ``query``, or None when it is not (only) a volume command."""
    q = _clean(query)
    if not q or ("volume" not in q and not re.search(r"\b(?:louder|quieter|softer|mute|unmute|un-mute|turn)\b", q)):
        return None
    for pattern in _SET:
        m = pattern.match(q)
        if m:
            return VolumeAsk("set", level=_level(m))
    for pattern, level in _NAMED_LEVELS:
        if pattern.match(q):
            return VolumeAsk("set", level=level)
    for pattern in _UP_DOWN:
        m = pattern.match(q)
        if m:
            groups = m.groupdict()
            up = groups.get("dir") == "up" or groups.get("word") == "louder"
            step = _amount(groups.get("amount") or groups.get("amount_pre"))
            return VolumeAsk("step", delta=step if up else -step)
    m = _MUTE.match(q)
    if m:
        return VolumeAsk("mute" if m.group("word") == "mute" else "unmute")
    return None


_PLACE_AT_END = re.compile(r"\s(?:on|in|for)\s+(?:the\s+)?(?P<place>[a-z0-9' ]+?)(?:\s+please)?[\s.!?]*$")
_DEVICE_VOLUME = re.compile(r"^(?:.*?\s)?(?:set|change|put|turn)\s+(?:the\s+)?(?P<place>[a-z0-9' ]+?)\s+volume\b")


def place(query: str) -> str | None:
    """The device or room a volume command names ("in the kitchen", "the den TV volume"), or None."""
    q = _clean(query)
    for m in (_PLACE_AT_END.search(q), _DEVICE_VOLUME.match(q)):
        name = m.group("place").strip() if m else ""
        if name not in ("", "the", "it", "this", "that", "here", "volume"):
            return name
    # "Mute the TV", "turn the TV up": the TV, not the speaker that heard it.
    return "tv" if re.search(r"\btv\b", q) else None


def transport_payload(ask: VolumeAsk, current: float | None) -> dict:
    """The MediaTransportRequest fields for ``ask``; a step uses the player's level when known."""
    if ask.kind == "set":
        return {"command": "volume_set", "volume_level": round(clamp(ask.level or 0.0), 3)}
    if ask.kind == "mute":
        return {"command": "volume_mute", "muted": True}
    if ask.kind == "unmute":
        return {"command": "volume_unmute", "muted": False}
    delta = ask.delta or 0.0
    if current is None:
        return {"command": "volume_up" if delta > 0 else "volume_down"}
    return {"command": "volume_set", "volume_level": round(clamp(current + delta), 3)}


def current_level(entity_id: str | None, entities: list[dict] | None) -> float | None:
    """The player's reported volume (0..1), or None."""
    entity = next((e for e in entities or [] if e.get("entity_id") == entity_id), None)
    value = ((entity or {}).get("attributes") or {}).get("volume_level")
    return float(value) if isinstance(value, (int, float)) else None
