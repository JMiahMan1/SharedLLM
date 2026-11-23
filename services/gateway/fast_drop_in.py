"""Tier-1 Drop In commands, matched without the model.

A Drop In is a short, unambiguous command ("drop in on the kitchen", "hang up"),
so it does not need a model turn. The matcher is deliberately conservative: a
phrase that could be an announcement ("drop in the laundry", "intercom the
kitchen dinner is ready") returns ``None`` and goes to the normal pipeline.
"""
from __future__ import annotations

import re
from typing import Any

_END_PHRASES = frozenset({
    "hang up",
    "hang up now",
    "hang up the call",
    "hang up the phone",
    "end call",
    "end drop in",
    "end drop-in",
    "end the call",
    "end the drop in",
    "end the drop-in",
    "end the drop in call",
    "end this call",
    "stop drop in",
    "stop drop-in",
    "stop the drop in",
    "stop the drop-in",
    "disconnect drop in",
    "disconnect the drop in",
})

_START_ON = re.compile(r"^drop\s+in\s+(?:on|to)\s+(.+)$")
_START_INTO = re.compile(r"^drop\s+into\s+(.+)$")
_START_BARE = re.compile(r"^drop\s+in\s+(.+)$")
_INTERCOM = re.compile(r"^intercom\s+(.+)$")
_START_PREPOSITION = re.compile(r"^(?:on|to|into)\b")

#: A bare preposition or article is never a room: "drop in on" must fall through
#: to the model instead of becoming a Drop In on a room called "on".
_STOP_TARGETS = frozenset({
    "on", "to", "into", "in", "at", "the", "a", "an", "for", "with", "my", "our",
})

#: A message body after the target means this is not a plain Drop In command.
_MESSAGE_BODY = re.compile(
    r"\b(?:tell|say|saying|announce|ask|remind|message|that|dinner|lunch|breakfast|ready|news)\b"
)


def _normalise(query: str) -> str:
    text = re.sub(r"\s+", " ", str(query or "").strip().lower())
    return text.strip(" .,!?;:")


def _target(text: str) -> str | None:
    target = re.sub(r"\s+", " ", text).strip(" .,!?;:")
    target = re.sub(r"\bplease$", "", target).strip(" .,!?;:")
    target = re.sub(r"^the\s+", "", target)
    return target or None


def _start(match: re.Match[str], *, bare: bool = False, intercom: bool = False) -> dict[str, Any] | None:
    raw = match.group(1).strip()
    # "drop in the laundry" is an announcement, not a room target. The
    # unambiguous "drop in on the laundry" still works.
    if bare and re.match(r"^the\b", raw):
        return None
    # "drop in on" (and "drop in to") has no target after the preposition, so it
    # falls through to the bare pattern. Never capture the preposition itself.
    if bare and _START_PREPOSITION.match(raw):
        return None
    target = _target(raw)
    if not target or target.lower() in _STOP_TARGETS:
        return None
    if _MESSAGE_BODY.search(target) or " and " in target:
        return None
    words = len(target.split())
    if intercom and words > 3:
        return None
    if bare and words > 4:
        return None
    return {"tool": "DropInRequest", "payload": {"action": "start", "target_room": target}}


def parse_drop_in_command(query: str) -> dict[str, Any] | None:
    """A ``DropInRequest`` call for a plain Drop In command, or ``None``.

    Returns ``{"tool": "DropInRequest", "payload": {...}}`` for a start or end,
    and ``None`` for anything that could carry a message or name no target.
    """
    text = _normalise(query)
    if not text:
        return None
    if text in _END_PHRASES:
        return {"tool": "DropInRequest", "payload": {"action": "end"}}
    if match := _START_ON.match(text):
        return _start(match)
    if match := _START_INTO.match(text):
        return _start(match)
    if match := _START_BARE.match(text):
        return _start(match, bare=True)
    if match := _INTERCOM.match(text):
        return _start(match, intercom=True)
    return None
