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


#: A play verb as speech recognition writes it: "Playin' music", "Playing", "Put on", "Can you start".
PLAY_VERB = r"(?:play(?:in[g'\u2019]?|s)?|put\s+on|start(?:\s+playing)?|throw\s+on|listen\s+to)"
_LEADING_VERB = re.compile(rf"^{PLAY_VERB}(?=[\s,]|$)[\s,]*", re.IGNORECASE)
#: Words before what is asked for that are never part of it ("me some", "us a little").
_LEADING_WORDS = re.compile(r"^(?:me|us|some|any)\b\s*", re.IGNORECASE)
#: Determiners and taste words that only qualify a genre or "music" ("a little", "some good").
_SOFT_WORDS = re.compile(
    r"^(?:a\s+little(?:\s+bit\s+of)?|a\s+bit\s+of|a\s+few|an?|the|little|good|nice|random|great)\b\s*",
    re.IGNORECASE)
_TRAILING_WORDS = re.compile(r"(?:[\s,]+(?:please|for\s+(?:me|us)|now|thanks|thank\s+you))+$", re.IGNORECASE)
#: "music by X", "something from X": the name after it is the request.
_BY_NAME = re.compile(r"^(?:music|songs?|tunes|something|anything|stuff|tracks?)\s+(?:by|from)\s+(?P<name>.+)$",
                      re.IGNORECASE)
#: Nouns that say "music" without naming any.
GENERIC_MEDIA_NOUNS = frozenset({"music", "songs", "song", "tunes", "tune", "something", "anything", "stuff",
                                 "tracks", "track"})
#: Genre and mood words: a request made only of these is searched as a genre (playlist or radio first),
#: never as a track title ("worship" is not the song "Call to Worship").
GENRE_WORDS = frozenset({
    "worship", "christian", "gospel", "praise", "hymn", "hymns", "spiritual", "church", "sunday",
    "relaxing", "relaxed", "calm", "calming", "chill", "peaceful", "quiet", "soft", "soothing", "mellow",
    "instrumental", "acoustic", "piano", "ambient", "background", "easy", "listening", "meditation",
    "kids", "kid", "children", "childrens", "children's", "kids'", "family", "lullaby", "lullabies", "nursery",
    "sleep", "sleepy", "study", "focus", "dinner", "party", "workout", "upbeat", "happy", "energetic",
    "classical", "jazz", "country", "rock", "pop", "folk", "blues", "soul", "funk", "rap", "hip", "hop",
    "hip-hop", "r&b", "reggae", "electronic", "dance", "oldies", "bluegrass", "latin", "lofi", "lo-fi",
    "christmas", "holiday", "contemporary", "classic", "modern", "old", "new", "morning", "evening",
    "80s", "90s", "70s", "60s", "2000s", "and",
})


@dataclass(frozen=True)
class MusicAsk:
    """What a spoken music request asks for.

    ``kind`` is "library" (nothing named: play something from the library), "genre" (only
    genre or mood words: search playlists and radio first) or "named" (an artist, album or
    track, searched as before). ``fallback`` is the one relaxed search to try when ``query``
    finds nothing.
    """

    kind: str
    query: str | None = None
    fallback: str | None = None


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", text.strip(" ,.!?\"'")) if w]


def _strip_repeat(pattern: re.Pattern, text: str) -> str:
    while True:
        new = pattern.sub("", text, count=1).strip(" ,")
        if new == text:
            return text
        text = new


def parse_music_request(text: str | None) -> MusicAsk:
    """The music a request asks for, from the whole sentence or the words after the verb.

    "Play some worship music." is the genre "worship music" (then "worship"); "Playin' music."
    and "Play music." name nothing; "Play Example Band" and "Always Holding On by Example
    Band" stay as they were said.
    """
    t = strip_request_filler(text or "")
    t = _strip_repeat(_LEADING_VERB, t)
    t = _TRAILING_WORDS.sub("", t).strip(" ,.!?\"'")
    t = _strip_repeat(_LEADING_WORDS, t)
    by = _BY_NAME.match(t)
    if by:
        name = by.group("name").strip(" ,.!?\"'")
        return MusicAsk("named", name) if name else MusicAsk("library")
    named_text = t  # an artist or title keeps its own "A" / "An" ("A Fake Band")
    soft = _strip_repeat(_SOFT_WORDS, t)
    words = _words(soft)
    if words and words[0].lower() in ("something", "anything") and len(words) > 1:
        words = words[1:]  # "something relaxing"
    head = list(words)
    while head and head[-1].lower() in GENERIC_MEDIA_NOUNS:
        head.pop()
    head = _words(_strip_repeat(_SOFT_WORDS, " ".join(head)))
    if not head:
        return MusicAsk("library")
    noun_dropped = len(head) < len(words)
    if all(w.lower().strip(",") in GENRE_WORDS for w in head) and not all(w.lower() == "and" for w in head):
        cleaned = " ".join(head)
        if noun_dropped:
            return MusicAsk("genre", " ".join(words), cleaned)
        return MusicAsk("genre", cleaned)
    if noun_dropped:
        # "Example Band songs": search the name; the words as said are the relaxed try.
        return MusicAsk("named", " ".join(head), " ".join(words))
    return MusicAsk("named", re.sub(r"^the\s+", "", named_text, flags=re.IGNORECASE) or named_text)


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


# --------------------------------------------------------------------------- #
# "In the kitchen": a room after what to play                                  #
# --------------------------------------------------------------------------- #

_IN_ROOM = re.compile(r"^(?P<what>.+?)\s+(?:in|into)\s+(?:the\s+|my\s+|our\s+)?(?P<room>[\w' -]{2,40}?)$", re.IGNORECASE)


def split_room(text: str | None, is_place) -> tuple[str | None, str | None]:
    """("some music", "kitchen") from "some music in the kitchen" when ``is_place`` knows the room.

    Only a room or player the household has counts, so "Murder in the Cathedral" and "Singing
    in the Rain" keep their titles.
    """
    m = _IN_ROOM.match((text or "").strip(" ,.!?"))
    if not m or len(m.group("room").split()) > 4:
        return text, None
    room = m.group("room").strip()
    try:
        known = bool(is_place(room))
    except Exception as exc:
        log.warning(f"[FastPath] could not check whether '{room}' is a room: {exc}")
        known = False
    return (m.group("what").strip(), room) if known else (text, None)


# --------------------------------------------------------------------------- #
# A question about what to play, and its answer                               #
# --------------------------------------------------------------------------- #

#: How long a "the song or the audiobook?" question waits for its answer.
CHOICE_TTL_S = 120
_YES = frozenset({"yes", "yeah", "yep", "sure", "please", "ok", "okay", "go ahead", "do it", "read it", "yes please"})


def _choice_key(user_id: str) -> str:
    return f"media:choice:{user_id}"


def remember_choice(user_id: str, choice: dict) -> None:
    """Keep execution's question (its options) until the person answers or it expires."""
    import json

    from services.gateway.cache import redis_cache_set

    redis_cache_set(_choice_key(user_id), json.dumps(choice), CHOICE_TTL_S)


def pending_choice(user_id: str) -> dict | None:
    import json

    from services.gateway.cache import redis_cache_get

    raw = redis_cache_get(_choice_key(user_id))
    try:
        data = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("options") else None


def clear_choice(user_id: str) -> None:
    from services.gateway.cache import redis_cache_delete

    redis_cache_delete(_choice_key(user_id))


_ORDINALS = {"first": 0, "1st": 0, "one": 0, "second": 1, "2nd": 1, "two": 1, "last": -1}


def match_choice(answer: str, choice: dict) -> dict | None:
    """The option ``answer`` names ("the audiobook", "the song", "the first one", "yes"), or None.

    An answer that names no option is not taken as one: it is a new request.
    """
    options = [o for o in choice.get("options") or [] if isinstance(o, dict)]
    text = " ".join(re.sub(r"[^a-z0-9' ]+", " ", strip_request_filler(answer).lower()).split())
    if not text or not options or len(text.split()) > 8:
        return None
    if len(options) == 1 and (text in _YES or text.startswith(("yes ", "yeah ", "sure "))):
        return options[0]
    hits = [o for o in options if any(re.search(rf"\b{re.escape(str(w).lower())}\b", text) for w in o.get("words") or [])]
    if len(hits) == 1:
        return hits[0]
    m = re.search(r"\b(first|1st|second|2nd|last|one|two)\b(?:\s+one)?", text)
    if m and len(text.split()) <= 4:
        idx = _ORDINALS[m.group(1)]
        return options[idx] if -len(options) <= idx < len(options) else None
    return None


# --------------------------------------------------------------------------- #
# "Play" heard as another word                                                 #
# --------------------------------------------------------------------------- #

#: Words speech recognition writes for a spoken "play" (over a TV speaker, or a quick "play"):
#: "Late <title>", "Player <name>". "Please" and "pray" are left out: they open too many
#: real requests.
PLAY_SOUNDALIKES = frozenset({"late", "plate", "played", "plays", "player", "clay", "lay", "slay", "flay",
                              "blay", "ply", "pley", "plait", "plane"})
#: A misheard request is played only when the library has this good a match for the rest.
MISHEARD_PLAY_MIN_SCORE = 0.85


def misheard_play(query: str) -> list[str]:
    """What may have been asked for when "play" was heard as another word, most likely first; [] when not.

    "Late call to harbor" -> ["call to harbor"]; "Player seven" -> ["seven", "er seven"] (the end of
    "player" may be the start of the name: "Play ARE7"). Only a library match decides
    (``MISHEARD_PLAY_MIN_SCORE``): these are guesses.
    """
    text = strip_request_filler(query or "")
    words = text.split()
    if len(words) < 2 or len(words) > 10:
        return []
    first = words[0].lower().strip(",.!?-")
    if first not in PLAY_SOUNDALIKES:
        return []
    rest = " ".join(words[1:]).strip(" ,.!?\"'")
    if not rest:
        return []
    guesses = [rest]
    if first == "player":
        guesses.append(f"er {rest}")  # "Player one": the "er" may be the start of the name
    return guesses
