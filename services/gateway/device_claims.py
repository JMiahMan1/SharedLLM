"""A device command the model answered may only claim what a tool did in that turn.

Seen on 2026-11-18, by voice: "Turn off the kitchen lights." The model tried one
bulb (unavailable), then said "The other three kitchen lights should be off now"
though nothing had switched them. Asked again a minute later it called no tool at
all and repeated that answer word for word: the voice history it is shown
(``orchestrator._voice_history``) held the same question with that answer, and at
temperature 0 the closest thing to the right reply was the reply already given.

Two small guards:

* ``drop_repeated_request``: a spoken request the person repeats is wanted
  again, so the earlier exchange for the same words is left out of the history the
  model reads (it is still in the stored history).
* ``honest_reply``: when the question was a turn on/off command, and the model's
  answer says a device is now on or off (or was turned/switched), but no device
  tool succeeded in this turn, the answer is replaced by what is true: nothing was
  switched, with the tool's own failure when one failed.
"""
from __future__ import annotations

import re

#: The single-turn tools that change a device (normalized names).
DEVICE_TOOLS = frozenset({
    "lightcontrolrequest", "haservicerequest", "climaterequest", "securityrequest", "nightmoderequest",
    "mediaplayrequest", "mediatransportrequest", "climatecontrolrequest",
})
#: How ``_execute_single_tool`` words a tool that did not do its job.
FAILED_PREFIXES = ("Sorry, I couldn't", "Sorry, I encountered", "I don't have a handler")

_COMMAND = re.compile(r"^(?:please\s+)?(?:can you\s+|could you\s+)?(?:turn|switch|power|shut)\b.*\b(on|off)\b",
                      re.IGNORECASE)
#: An answer saying a device changed or is now in a state.
_CLAIM = re.compile(
    r"\b(?:(?:is|are|was|were|'s|'re|should be|will be|now|have been|has been)\s+(?:now\s+)?(?:turned\s+|switched\s+)?"
    r"(?:on|off)\b|(?:turned|switched|shut)\s+(?:\w+\s+){0,4}?(?:on|off)\b|i(?:'ve| have)\s+(?:turned|switched))",
    re.IGNORECASE,
)


def _words(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def device_command(query: str) -> str | None:
    """``"on"`` / ``"off"`` for a turn on/off command; None for anything else (a question included)."""
    q = (query or "").strip()
    if q.endswith("?"):
        return None
    m = _COMMAND.match(q)
    return m.group(1).lower() if m else None


def drop_repeated_request(history: list, query: str) -> list:
    """The history without earlier exchanges whose request was these same words."""
    want = _words(query)
    if not want:
        return history
    out: list = []
    skip_answer = False
    for message in history:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user":
            skip_answer = _words(str(message.get("content") or "")) == want
            if skip_answer:
                continue
        elif skip_answer and message.get("role") == "assistant":
            skip_answer = False
            continue
        out.append(message)
    return out


def tool_succeeded(result: str | None) -> bool:
    return bool(result) and not str(result).startswith(FAILED_PREFIXES)


def honest_reply(query: str, answer: str, device_runs: list[tuple[str, str]]) -> str:
    """``answer``, unless it claims a device change for a device command that no tool made.

    ``device_runs`` is ``(tool, result)`` for every device tool run in this turn.
    """
    state = device_command(query)
    if state is None or not _CLAIM.search(answer or ""):
        return answer
    if any(tool_succeeded(result) for _tool, result in device_runs):
        return answer
    failed = next((r for _t, r in device_runs if r), "")
    if failed:
        reason = re.sub(r"^Sorry, I (?:couldn't complete that action|encountered an error[^:]*)[:.]?\s*", "",
                        failed).strip()
        first = re.split(r"(?<=[.!?])\s+", reason, maxsplit=1)[0] if reason else ""
        return f"I couldn't turn that {state}, so nothing was switched. {first}".strip()
    return f"I didn't switch anything just now, so I can't say it's {state}. Please ask me again."


# --------------------------------------------------------------------------- actions claimed with no tool

#: The one extra instruction when an answer claims an action no tool did (see ``claims_action``).
NO_TOOL_NUDGE = ("You did not call any tool, so nothing was done. Call the tool now, "
                 "or say plainly that you could not do it.")

_ACTION_DONE = (r"play(?:ed|ing)?|start(?:ed|ing)|turn(?:ed|ing)\s+(?:\w+\s+){0,4}?(?:on|off)|switch(?:ed|ing)"
                r"\s+(?:\w+\s+){0,4}?(?:on|off)|set(?:ting)?|search(?:ed|ing)|look(?:ed|ing)\s+up|read(?:ing)?"
                r"|open(?:ed|ing)|sen(?:t|ding)|add(?:ed|ing)|sav(?:ed|ing)|queu(?:ed|ing)|put(?:ting)?\s+on")
#: Leading words an assistant puts before what it did ("Okay, playing ...").
_ACK = r"(?:(?:ok(?:ay)?|sure(?:\s+thing)?|alright|all\s+right|done|got\s+it|you\s+got\s+it|certainly|absolutely)[,.!]?\s+)*"
_CLAIMED_ACTION = re.compile(
    rf"^{_ACK}(?:now\s+)?(?:play(?:ing|ed)|start(?:ing|ed)|turn(?:ing|ed)\s|switch(?:ing|ed)\s|setting\s|search(?:ing|ed)\s"
    rf"|reading\s|open(?:ing|ed)\s|sending\s|sent\s|adding\s|added\s|saving\s|saved\s|queu(?:ing|ed)\s)"
    rf"|\bi(?:'ve|\s+have|'m|\s+am)?\s+(?:just\s+|now\s+|also\s+)?(?:{_ACTION_DONE})\b"
    rf"|\b(?:is|are)\s+now\s+(?:playing|on|off|set)\b|\bnow\s+playing\b"
    rf"|\b(?:has|have)\s+been\s+(?:played|started|turned|switched|set|sent|added|saved|opened|queued)\b",
    re.IGNORECASE,
)
#: A sentence that asks, offers or says it could not: not a claim that something was done.
_NOT_A_CLAIM = re.compile(
    r"\b(?:would\s+you|do\s+you\s+want|want\s+me\s+to|shall\s+i|should\s+i|i\s+can|i\s+could|i\s+would"
    r"|i(?:'ll|\s+will)|if\s+you(?:'d|\s+would)?\s+like|let\s+me\s+know|can't|cannot|couldn't|could\s+not"
    r"|unable|not\s+able|didn't|did\s+not|haven't|have\s+not|wasn't|was\s+not|won't|don't|do\s+not"
    r"|i'd\s+be\s+happy|happy\s+to)\b",
    re.IGNORECASE,
)


def claims_action(answer: str | None) -> bool:
    """True when ``answer`` says an action was done ("Playing worship music in the office.", "I searched
    the web..."), as opposed to asking, offering or saying it could not.

    Word lists only, so it costs nothing; the caller asks the model again only when no tool ran.
    """
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", (answer or "").strip()):
        s = sentence.strip(" *-\"'")
        if not s or s.endswith("?") or _NOT_A_CLAIM.search(s):
            continue
        if _CLAIMED_ACTION.search(s):
            return True
    return False
