"""A spoken reply, a sentence at a time, for a voice satellite (``/api/chat`` with ``"speech_stream": true``).

A voice satellite waited for the whole answer before it said a word: the model took 12 s on a typical
turn and up to 33 s (audit of 2026-11-19), while speech to text took 1-3 s and speaking a sentence well
under one. The hub can start speaking the first sentence while the model writes the rest, as HA does
with streaming TTS, if the gateway hands it sentences as they are written.

The chat path already streams the model's words for chat windows, but those words are not always the
answer: a turn can be a tool call (JSON), and the answer to a device command is checked afterwards by
``device_claims.honest_reply``, which may replace it. So the hub is not given raw words. This gate sits
between the model and the stream and lets through only what can be said safely:

* whole sentences of prose (``MIN_CHARS`` or more, so "1." or "Okay." is not its own clip);
* nothing in a turn once it starts writing a tool call (``_TOOL_MARKERS``) or a reasoning block;
* nothing at all for a turn on/off command, because ``honest_reply`` may still change that answer.

Each new model call (after a tool ran, a retry, the closing turn) starts with a ``speech_turn`` marker.
When the job ends, the chat handler compares the final answer with what was said since the last
marker (``speech_rest``) and sends the rest, so the end of the answer, and any answer that differs from
what was streamed, is still spoken whole, once.
"""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from services.gateway.device_claims import device_command

#: Text that starts a tool call or markup the hub must not read out: nothing after it in that turn is spoken.
_TOOL_MARKERS = ("{", "```", "<tool", "[TOOL", "tool_code", "<|")
#: The shortest piece of text sent as its own sentence; shorter ones wait for the next.
MIN_CHARS = 20
#: The end of a sentence: . ! ? (with closing quotes or brackets) before a space, or a line break.
_BOUNDARY = re.compile("[.!?\\u2026][\"'\\u201d\\u2019)\\]]*(?=\\s)|\\n")
#: Words a full stop does not end a sentence after.
_ABBREVIATIONS = frozenset({"mr", "mrs", "ms", "dr", "st", "vs", "etc", "eg", "ie", "jr", "sr", "no", "approx"})
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

Emit = Callable[[dict[str, Any]], Awaitable[None]]


def _piece_text(chunk: Any) -> str:
    """The answer text in one chunk a provider streams (a dict, its JSON, or plain text); "" for thinking."""
    if isinstance(chunk, str) and chunk.startswith('{"type":'):
        try:
            chunk = json.loads(chunk)
        except ValueError:
            return chunk
    if isinstance(chunk, dict):
        return str(chunk.get("text") or "") if chunk.get("type", "content") == "content" else ""
    return str(chunk or "")


class SpeechGate:
    """Turns the model's streamed words into ``{"type": "speech", "text": sentence}`` chunks, safely."""

    def __init__(self, emit: Emit, query: str) -> None:
        self.emit = emit
        #: A device command's answer may be replaced afterwards (device_claims), so none of it is streamed.
        self.enabled = device_command(query) is None
        self.turns = 0
        self._buf = ""
        self._blocked = False

    async def begin_turn(self) -> None:
        """A new model call: what it says replaces nothing already said, and starts unblocked."""
        if self.turns and self.enabled:
            await self.emit({"type": "speech_turn"})
        self.turns += 1
        self._buf, self._blocked = "", False

    async def feed(self, chunk: Any) -> None:
        """One streamed piece of the model's answer (the provider's ``chunk_callback``)."""
        text = _piece_text(chunk)
        if not self.enabled or self._blocked or not text:
            return
        self._buf += text
        for sentence in self._take():
            await self.emit({"type": "speech", "text": sentence})

    def _take(self) -> list[str]:
        buf = self._buf
        lowered = buf.lower()
        if "<think>" in lowered:
            if "</think>" not in lowered[lowered.rfind("<think>"):]:
                return []  # a reasoning block still being written
            buf = _THINK.sub("", buf)
        cut = min((i for i in (buf.find(m) for m in _TOOL_MARKERS) if i >= 0), default=-1)
        speakable = buf if cut < 0 else buf[:cut]
        out: list[str] = []
        start = 0
        for m in _BOUNDARY.finditer(speakable):
            end = m.end()
            piece = speakable[start:end].strip()
            if len(piece) < MIN_CHARS or _ends_with_abbreviation(piece):
                continue
            out.append(piece)
            start = end
        if cut >= 0:
            self._blocked = True  # the rest of this turn is a tool call; what led into it is dropped
            self._buf = ""
        else:
            self._buf = buf[start:]
        return out


def _ends_with_abbreviation(piece: str) -> bool:
    if not piece.endswith("."):
        return False
    words = piece[:-1].split()
    if not words:
        return True
    last = re.sub(r"[^a-z]", "", words[-1].lower())
    return len(last) == 1 or last in _ABBREVIATIONS


def _words(text: str) -> list[tuple[str, int]]:
    """Each word of ``text``, lower-cased without punctuation, with where it ends in ``text``."""
    out = []
    for m in re.finditer(r"\S+", text or ""):
        word = re.sub(r"[^\w]", "", m.group().lower())
        if word:
            out.append((word, m.end()))
    return out


def speech_rest(reply: str, spoken: list[str]) -> str:
    """What of ``reply`` is still to be said, after the sentences ``spoken`` in its turn.

    The spoken sentences are matched word for word against the start of the reply (punctuation and
    case ignored). When they are not its start (the answer was cleaned or replaced after streaming),
    the whole reply is the rest: saying something twice beats leaving the answer out.
    """
    reply = (reply or "").strip()
    said = [w for s in spoken for w, _ in _words(s)]
    if not said:
        return reply
    words = _words(reply)
    if len(words) < len(said) or [w for w, _ in words[:len(said)]] != said:
        return reply
    return reply[words[len(said) - 1][1]:].strip()


__all__ = ["MIN_CHARS", "SpeechGate", "speech_rest"]
