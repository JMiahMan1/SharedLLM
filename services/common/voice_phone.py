"""What a phone may say about its own voice turn (docs/VOICE_PHONE.md).

An Echo's room, its satellite and its speaker come from HomeHub, which holds the
internal secret. The room decides what "the lights" means, the satellite decides
where a timer rings and where unnamed media plays, and the speaker decides what a
request is allowed to do. A caller that could invent any of them could act in
another room, as another person - so the gateway has always refused every voice
context that did not come from the hub.

A phone is a different kind of caller. It is signed in as one person, it knows
which device it is, and it may say which room it is standing in (the room
beacons already tell the household that). It may never name an Echo's satellite
or media player. That is the whole rule, kept here so it can be read and tested
without a gateway in front of it.
"""

from __future__ import annotations

import re
from typing import Any

#: What a phone sends to say "this is my own turn", never an Echo's.
SOURCE = "android"

#: The fields a phone may report. ``satellite`` and ``media_player`` are absent
#: on purpose: naming one would move a timer or a speaker into another room.
CLAIMABLE = ("area", "area_id", "device_name", "asked", "extra_system_prompt")

_LIMITS = {"area": 80, "area_id": 80, "device_name": 80, "asked": 500, "extra_system_prompt": 1000}

#: A conversation id the app made up: no separators, no room for anything else.
_CONVERSATION = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def app_context(raw: Any, *, authenticated: bool) -> dict[str, Any] | None:
    """The context of a signed-in phone's voice turn, or None to drop it.

    Dropping is the safe answer, and the one the gateway has always given: a
    request with no context is answered as that person's own request with no room
    attached, exactly as typed chat is.
    """
    if not authenticated or not isinstance(raw, dict) or raw.get("source") != SOURCE:
        return None
    out: dict[str, Any] = {}
    for key in CLAIMABLE:
        value = raw.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            text = str(value).strip()[: _LIMITS[key]]
            if text:
                out[key] = text
    conversation = raw.get("conversation_id")
    if isinstance(conversation, str) and _CONVERSATION.match(conversation):
        out["conversation_id"] = conversation
    if raw.get("whispered") is True:
        out["whispered"] = True
    return out or None
