"""Spoken media requests on the fast path: which player, what to play, and audiobooks.

Three things a voice satellite gets wrong without this:

* **Where.** "Play music" said to a satellite with its own speaker plays on that
  speaker (``voice_context["media_player"]``, sent by HomeHub), not on whatever
  player the household used last. A device named in the request still wins.
* **What.** "Can you play music?" is not a search for the words "Can you play
  music". Politeness and filler are stripped (``strip_request_filler``), and a
  request that names nothing (``is_generic_music``) asks execution for something
  from the library instead of searching.
* **Audiobooks.** "Play my latest audiobook", "the audiobook by <author>", "<title>
  audiobook" go to Audiobookshelf, not Music Assistant (``parse_audiobook_request``).
  "My" needs to know who is speaking: the household cannot have a "my".
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("gateway.fast_media")

#: The identity a voice satellite answers as when it did not recognise the speaker (identity HOUSEHOLD_IDENTITY).
HOUSEHOLD_USER = "household"
#: States a player can be told to pause / skip in.
ACTIVE_STATES = frozenset({"playing", "paused", "buffering"})

_LEADING_FILLER = re.compile(
    r"^(?:(?:hey|ok|okay|hi)\s+)?(?:jarvis\b[\s,.!]*)?"
    r"(?:(?:can|could|would|will)\s+you\s+|please\s+|(?:i(?:'d|\s+would)\s+like|i\s+want)\s+(?:you\s+)?to\s+|"
    r"let'?s\s+|go\s+ahead\s+and\s+|just\s+)",
    re.IGNORECASE,
)
_TRAILING_FILLER = re.compile(r"(?:[\s,]+(?:please|for\s+me|thanks|thank\s+you|now|jarvis))+$", re.IGNORECASE)
_DEVICE_CLAUSE = re.compile(r"\s+on\s+\S", re.IGNORECASE)

_GENERIC_MUSIC = re.compile(
    r"^(?:(?:me|us)\s+)?(?:some|a|any|the)?\s*(?:good|nice|random|great)?\s*"
    r"(?:music|songs?|tunes?|something|anything|a\s+song|some\s+music)"
    r"(?:\s+(?:music|songs?))?$",
    re.IGNORECASE,
)

_BOOK = r"(?:audio\s*-?\s*books?|books?)"
_AUDIO_BOOK = r"(?:audio\s*-?\s*books?)"
_LATEST = r"(?:latest|newest|new|last|most\s+recent|recent|current)"


@dataclass(frozen=True)
class AudiobookAsk:
    """What a spoken audiobook request asks for."""

    kind: str          # "resume" | "author" | "series" | "title"
    value: str = ""    # the author, series or title as heard
    mine: bool = False  # "my": needs to know who is speaking
    latest: bool = False


def strip_request_filler(query: str) -> str:
    """The command without "Jarvis, can you ... please?" around it.

    A trailing "Jarvis" stays when the sentence names a device ("on Office,
    Jarvis"): there it may be part of the device's name.
    """
    text = (query or "").strip().strip("?.!").strip()
    while True:
        new = _LEADING_FILLER.sub("", text, count=1).strip()
        if new == text:
            break
        text = new
    if _DEVICE_CLAUSE.search(text):
        text = re.sub(r"(?:[\s,]+(?:please|for\s+me|thanks|thank\s+you|now))+$", "", text, flags=re.IGNORECASE)
    else:
        text = _TRAILING_FILLER.sub("", text)
    return text.strip(" ,.!?")


def is_generic_music(media_query: str | None) -> bool:
    """True when what was asked for names nothing: "music", "a song", "something"."""
    text = (media_query or "").strip(" ,.!?\"'")
    return not text or bool(_GENERIC_MUSIC.match(text))


def _split_device(text: str) -> tuple[str, str | None]:
    """("<request>", "<device>") from "<request> on <device>", for a short device phrase."""
    m = re.match(r"^(.+?)\s+on\s+(?:the\s+)?(.{1,40})$", text, flags=re.IGNORECASE)
    if m and len(m.group(2).split()) <= 4:
        return m.group(1).strip(), m.group(2).strip(" ,")
    return text, None


def _name(value: str) -> str:
    return " ".join(value.replace(".", " ").split()).strip(" ,'\"")


def parse_audiobook_request(query: str, is_device=None) -> tuple[AudiobookAsk, str | None] | None:
    """(what is asked, named device) for an audiobook request, or None when it is not one.

    "... on <device>" names a device only when ``is_device`` (when given) knows it, so
    "Murder on the Orient Express audiobook" keeps its title.
    """
    full = strip_request_filler(query)
    head, device = _split_device(full)
    if device and (is_device is None or is_device(device)):
        ask = _parse_audiobook(head)
        if ask is not None:
            return ask, device
    ask = _parse_audiobook(full)
    return (ask, None) if ask is not None else None


def _parse_audiobook(text: str) -> AudiobookAsk | None:
    t = text.strip()
    if not t:
        return None
    resume = (
        re.fullmatch(rf"(?:play|continue|resume|start|put\s+on)\s+(?P<who>my|our|the)?\s*(?P<l>{_LATEST})?\s*"
                     rf"(?:{_BOOK}|story)", t, re.IGNORECASE)
        or re.fullmatch(rf"(?:keep|continue|resume|go\s+on)\s+(?P<who>)(?P<l>)(?:reading|listening)"
                        rf"(?:\s+(?:to\s+)?(?:my|the)\s+{_BOOK})?", t, re.IGNORECASE)
    )
    if resume:
        who = (resume.group("who") or "").lower()
        mine = who in ("my", "our") or (not who and t.lower().split()[0] in ("keep", "continue", "resume", "go"))
        return AudiobookAsk("resume", mine=mine, latest=True)
    m = re.fullmatch(rf"(?:play|read|start|put\s+on)\s+(?:me\s+)?(?:the|an?|my|some)?\s*(?P<l>{_LATEST})?\s*"
                     rf"{_BOOK}\s+(?:by|from)\s+(?P<who>.+)", t, re.IGNORECASE)
    if m and _name(m.group("who")):
        return AudiobookAsk("author", _name(m.group("who")), latest=bool(m.group("l")))
    m = re.fullmatch(rf"(?:play|read|start|put\s+on)\s+(?:me\s+)?(?P<who>.+?)'s\s+(?P<l>{_LATEST})?\s*{_BOOK}",
                     t, re.IGNORECASE)
    if m and _name(m.group("who")) and m.group("who").lower() not in ("my", "our"):
        return AudiobookAsk("author", _name(m.group("who")), latest=bool(m.group("l")))
    m = re.fullmatch(rf"(?:play|read|start|put\s+on)\s+(?:me\s+)?(?:the\s+)?(?:next|{_LATEST})?\s*{_BOOK}\s+"
                     rf"(?:in|from)\s+(?:the\s+)?(?P<series>.+?)(?:\s+series)?", t, re.IGNORECASE)
    if m and _name(m.group("series")):
        return AudiobookAsk("series", _name(m.group("series")))
    m = (re.fullmatch(rf"(?:play|read|start)\s+(?:me\s+)?(?:the\s+)?{_AUDIO_BOOK}\s+(?P<title>.+)", t, re.IGNORECASE)
         or re.fullmatch(r"(?:play|start)\s+(?:me\s+)?the\s+book\s+(?P<title>.+)", t, re.IGNORECASE)
         or re.fullmatch(rf"(?:play|read|start)\s+(?:me\s+)?(?P<title>.+?)\s+(?:the\s+)?(?:as\s+an?\s+)?{_AUDIO_BOOK}",
                         t, re.IGNORECASE))
    if m and m.group("title").strip(" ,'\""):
        return AudiobookAsk("title", m.group("title").strip(" ,'\""))
    return None


def unknown_speaker_reply() -> str:
    """Why "my audiobook" did nothing: the satellite does not know whose it is."""
    return ("I didn't recognise your voice, so I don't know whose audiobook to continue. Say the title "
            "or the author instead, or say 'learn my voice' so I know you next time.")


def audiobook_payload(ask: AudiobookAsk, base: dict) -> dict:
    """The /execute/audiobookshelf request for ``ask``; ``base`` carries user_context, entity_id, device_name."""
    if ask.kind == "resume":
        return {**base, "action": "resume"}
    if ask.kind == "author":
        return {**base, "action": "play_by", "author": ask.value, "latest": ask.latest}
    if ask.kind == "series":
        return {**base, "action": "play_by", "series": ask.value, "latest": ask.latest}
    return {**base, "action": "play", "query": ask.value}


#: voice_satellite_playback values: media asked for at a satellite with no device named plays on the
#: satellite's own speaker ("this_device", the default) or on the household's last-used player ("last_used").
SATELLITE_PLAYBACK = frozenset({"this_device", "last_used"})


def satellite_player(voice_context: dict | None, entities: list[dict] | None) -> tuple[str | None, str | None]:
    """(entity id, state) of the satellite's own speaker when it can be used, else (None, None).

    Only a player this person can see (it is among ``entities``) and that answers.
    """
    eid = (voice_context or {}).get("media_player") if isinstance(voice_context, dict) else None
    if not eid:
        return None, None
    entity = next((e for e in entities or [] if e.get("entity_id") == eid), None)
    if entity is None:
        log.info(f"[FastPath] satellite speaker {eid} is not among the players this request may use")
        return None, None
    state = str(entity.get("state") or "").lower()
    if state in ("unavailable", "unknown"):
        log.info(f"[FastPath] satellite speaker {eid} is {state}; not using it")
        return None, None
    return eid, state


_POWER_OBJECT = re.compile(
    r"^(?:turn|switch|power)\s+(?:it\s+)?(?:on|off)(?:\s+(?:the\s+)?(?P<obj>.*?))?(?:\s+(?:on|off))?$", re.IGNORECASE)
_POWER_GENERIC = {"", "it", "this", "that", "music", "the music", "audio", "the audio", "speaker", "the speaker",
                  "media", "the media", "sound", "the sound"}


def power_names_no_device(query: str) -> bool:
    """True for "turn it off" / "turn off the music": a power command that names no particular device."""
    m = _POWER_OBJECT.match(strip_request_filler(query))
    return bool(m) and (m.group("obj") or "").strip().lower() in _POWER_GENERIC
