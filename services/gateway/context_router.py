"""Which retrieval collections a question may pre-fetch from, decided by what it asks.

The old rule searched a standing set (devices, files, capabilities, lessons) for
every question and kept whatever came back: the vector index always returns its
nearest rows, so "What's the verse of the day?" arrived with 45 unrelated hits.
Now a question pre-fetches only from collections its intent names, a little
(``RAG_PREFETCH_K`` hits each, above a similarity floor, inside a character
budget), and everything else is fetched on demand through the tools the
knowledge map names. A question that names no source pre-fetches nothing.

The intent comes from the FastPath classifier when the caller has it (the chat
handler classifies every question before it is queued) and from plain wording
otherwise. Wording is matched as whole words: every collection costs a search on
the reply's path, voice included, so a common word must not pull one in.
"""
from __future__ import annotations

import re

from services.common.rag_collections import HOME_DEVICES

#: Classifier intents (data/phrasebook.json) and the collection each pre-fetches.
#: Below ``INTENT_MIN_CONFIDENCE`` an intent is a guess and routes nothing.
INTENT_COLLECTIONS: dict[str, str] = {
    "turn_on": HOME_DEVICES,
    "turn_off": HOME_DEVICES,
    "ha_status": HOME_DEVICES,
    "play_media": HOME_DEVICES,
    "pause_media": HOME_DEVICES,
    "media_transport": HOME_DEVICES,
    "storage_search": "nextcloud_files",
    "workspace_coding": "system_learnings",
}
INTENT_MIN_CONFIDENCE = 0.6


def _words(*phrases: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(re.escape(p) for p in phrases) + r")\b")


#: Wording that names a source. Device nouns are things a person controls or asks the
#: state of; "door" and "lock" are here because "is the back door locked" is a device question.
_DEVICE_WORDS = _words(
    "light", "lights", "lamp", "lamps", "switch", "switches", "fan", "fans", "thermostat",
    "heater", "heat pump", "air conditioning", "a/c", "lock", "locks", "locked", "unlock",
    "unlocked", "door", "doors", "garage", "blinds", "shades", "curtains", "tv", "television",
    "speaker", "speakers", "plug", "outlet", "sensor", "sensors", "camera", "cameras",
    "alarm system", "vacuum", "sprinkler", "sprinklers", "device", "devices",
)
_FILE_WORDS = _words(
    "file", "files", "document", "documents", "pdf", "spreadsheet", "folder", "nextcloud",
    "recipe", "recipes",
)
_CODING_WORDS = _words(
    "code", "git", "workspace", "bug", "traceback", "stack trace", "refactor", "repo", "repository",
)
#: Not a bare "can you": that opens most commands ("can you turn on the light").
_CAPABILITY_WORDS = _words(
    "what can you do", "what else can you do", "can you do", "are you able to", "do you support",
    "how do i use", "what tools", "your features", "your capabilities",
)

#: Specialised collections, searched only when the question plainly asks about that source.
_SIGNAL_COLLECTIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("user_facts", _words(
        "about me", "who am i", "do you remember", "what do you remember", "remember that i",
        "remember my", "my preference", "my preferences", "i prefer", "my favorite",
        "my favourite", "my allergy", "my allergies", "allergic", "my birthday", "what do i like",
    )),
    ("mission_history", _words(
        "raven mission", "raven missions", "past mission", "last mission", "previous mission",
        "post-mortem", "postmortem", "previous run", "last run",
    )),
    ("network_topology", _words(
        "docker", "container", "containers", "subnet", "vlan", "ip address",
        "network topology", "open ports", "which port",
    )),
    ("telemetry_alerts", _words(
        "alerts", "telemetry", "anomaly", "anomalies", "went offline", "gone offline",
    )),
    ("conversation_memory", _words(
        "intercom", "transcript", "who said", "what did we say", "what did i say",
    )),
)


#: Questions one tool answers outright, from a source that is not in the RAG index at all
#: (scripture text lives in the Bible service, not in calibre_files). They pre-fetch nothing,
#: even though their words ("verse") would otherwise summon book passages.
_TOOL_ANSWERS = _words(
    "verse of the day", "verse for today", "today's verse", "daily verse", "votd",
    "reading of the day", "today's reading",
)


def tool_answers(query: str) -> bool:
    """True when ``query`` is one a single tool answers directly (see ``_TOOL_ANSWERS``)."""
    return bool(_TOOL_ANSWERS.search((query or "").lower()))


def route_collections(
    query: str,
    *,
    intent: str | None = None,
    confidence: float = 0.0,
    library: bool = False,
    hub: bool = True,
    devices_first: bool = False,
) -> list[str]:
    """The collections ``query`` may pre-fetch from, most relevant first.

    ``library`` puts the book collection first (the caller knows books are wanted,
    or the wording asked for them). ``hub`` False drops the device index: with no
    home hub nothing in it can be read or controlled.
    """
    q = (query or "").lower()
    chosen: list[str] = []
    if tool_answers(q) and not library:
        return chosen

    def add(coll: str) -> None:
        if coll not in chosen:
            chosen.append(coll)

    if library:
        add("calibre_files")
    if intent and confidence >= INTENT_MIN_CONFIDENCE and intent in INTENT_COLLECTIONS:
        add(INTENT_COLLECTIONS[intent])
    if _DEVICE_WORDS.search(q):
        add(HOME_DEVICES)
    if _FILE_WORDS.search(q):
        add("nextcloud_files")
    if _CAPABILITY_WORDS.search(q):
        add("system_capabilities")
    if _CODING_WORDS.search(q):
        add("system_learnings")
    for coll, pattern in _SIGNAL_COLLECTIONS:
        if pattern.search(q):
            add(coll)
    if not hub:
        chosen = [c for c in chosen if c != HOME_DEVICES]
    if devices_first and HOME_DEVICES in chosen:
        chosen = [HOME_DEVICES] + [c for c in chosen if c != HOME_DEVICES]
    return chosen


#: Wording of a request that may use the workspace protocol and toolchain (Raven's curriculum).
_WORKSHOP_WORDS = _words(
    "code", "script", "program", "git", "github", "workspace", "repo", "repository", "build",
    "compile", "deploy", "shell", "terminal", "command line", "bug", "traceback", "stack trace",
    "refactor", "python", "javascript", "html", "website", "app", "transcribe", "convert",
    "ffmpeg", "mission",
)


def wants_curriculum(query: str, *, intent: str | None = None, confidence: float = 0.0) -> bool:
    """Whether a chat question is about building or running something (code, files in a
    workspace, a conversion), the only chat turns the workspace curriculum helps."""
    if intent == "workspace_coding" and confidence >= INTENT_MIN_CONFIDENCE:
        return True
    return bool(_WORKSHOP_WORDS.search((query or "").lower()))


def normalized(text: str) -> str:
    """A hit's text for de-duplication across collections."""
    return " ".join((text or "").lower().split())


def clip(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters at a word, saying that it was cut."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " ... [cut]"
