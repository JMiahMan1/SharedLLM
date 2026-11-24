"""The library's proper nouns: hints for speech to text, and a second chance for a misheard name.

Speech to text hears names it has never seen as ordinary words ("Brandon Lake" as
"brand in Lake", "Jenny B. Jones" as "Ginny B. Jones"), and a library search for those
words finds nothing. Two things help:

* ``hints()`` / ``initial_prompt()``: a bounded list of the names in the household's
  libraries (artists, authors, audiobook and series titles, playlists) for the speech to
  text server to bias towards (``wyoming_stt``). Whisper's prompt is cut off past about
  220 tokens and long prompts make it echo words nobody said, so the list is capped by
  count and by characters, most recently seen names first.
* ``best_match()``: before a search gives up, the spoken phrase is compared with the
  names the library has, and a close, unambiguous match is searched instead (logged).

The names are learnt from the listings the media handlers already fetch (Music
Assistant's library, Audiobookshelf's items) and kept in memory: speech to text runs in
this same execution process. Nothing here invents a name; an empty vocabulary only means
no hints and no corrections.
"""
from __future__ import annotations

import difflib
import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable

log = logging.getLogger("execution.media_vocabulary")

#: Kinds of name, in the order the hint list prefers them when it is cut short.
KINDS = ("artist", "author", "series", "playlist", "title")
#: The most names kept per kind; the oldest seen go first.
MAX_PER_KIND = 500
#: The hint list sent to speech to text: at most this many names ...
MAX_HINT_NAMES = 60
#: ... and at most this many characters (about 200 Whisper tokens at two characters a token, an over-estimate).
MAX_HINT_CHARS = 400
#: A name longer than this is a sentence, not a name worth biasing towards.
MAX_NAME_CHARS = 60
#: Names refresh whenever a handler lists a library; past this age a kind counts as stale.
STALE_AFTER_S = 6 * 3600
#: How alike a spoken phrase and a name must be (difflib ratio over letters and digits) ...
MATCH_CUTOFF = 0.8
#: ... and by how much the best must beat the next best to be taken (no coin flips).
MATCH_MARGIN = 0.05

_lock = threading.Lock()
_names: dict[str, OrderedDict[str, float]] = {k: OrderedDict() for k in KINDS}
_refreshed: dict[str, float] = {}


def _clean(name: object) -> str:
    text = " ".join(str(name or "").split())
    return text if 0 < len(text) <= MAX_NAME_CHARS else ""


def squash(text: str) -> str:
    """Lower-case letters and digits only: how a name sounds is closer to this than to its spelling."""
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def remember(kind: str, names: Iterable[object]) -> int:
    """Keep ``names`` of ``kind`` (an artist list, an author list ...); returns how many were new."""
    if kind not in _names:
        raise ValueError(f"unknown kind of name {kind!r}; one of {', '.join(KINDS)}")
    now = time.time()
    added = 0
    with _lock:
        bucket = _names[kind]
        for raw in names:
            name = _clean(raw)
            if not name:
                continue
            if name not in bucket:
                added += 1
            bucket[name] = now
            bucket.move_to_end(name)
        while len(bucket) > MAX_PER_KIND:
            bucket.popitem(last=False)
        _refreshed[kind] = now
    if added:
        log.info("[vocabulary] learnt %d new %s name(s); %d kept", added, kind, len(_names[kind]))
    return added


def names(*kinds: str) -> list[str]:
    """Every kept name of these kinds (all kinds when none are given), most recent first."""
    with _lock:
        out: list[str] = []
        for kind in kinds or KINDS:
            out.extend(reversed(_names.get(kind, OrderedDict())))
        return out


def stale(kind: str) -> bool:
    """True when ``kind`` has not been refreshed recently (or ever)."""
    return time.time() - _refreshed.get(kind, 0.0) > STALE_AFTER_S


def hints(max_names: int = MAX_HINT_NAMES, max_chars: int = MAX_HINT_CHARS) -> list[str]:
    """A bounded list of names for speech to text, taking from each kind in turn."""
    pools = [names(kind) for kind in KINDS]
    out: list[str] = []
    seen: set[str] = set()
    used = 0
    while len(out) < max_names and any(pools):
        for pool in pools:
            if not pool or len(out) >= max_names:
                continue
            name = pool.pop(0)
            key = name.lower()
            if key in seen:
                continue
            cost = len(name) + 2
            if used + cost > max_chars:
                return out
            seen.add(key)
            out.append(name)
            used += cost
    return out


def initial_prompt() -> str:
    """The hint list as one prompt line ("" when nothing has been learnt yet)."""
    listed = hints()
    return (", ".join(listed) + ".") if listed else ""


def best_match(phrase: str, candidates: Iterable[str], cutoff: float = MATCH_CUTOFF) -> str | None:
    """The candidate a misheard ``phrase`` most likely meant, or None when none is close and clear.

    Compared over letters and digits only, so "brand in Lake" meets "Brandon Lake"
    and "Ginny B. Jones" meets "Jenny B. Jones". An exact match is not a correction
    and comes back as is.
    """
    want = squash(phrase)
    if len(want) < 3:
        return None
    scored: dict[str, tuple[float, str]] = {}
    for cand in candidates:
        key = squash(cand)
        if not key:
            continue
        if key == want:
            return cand
        ratio = difflib.SequenceMatcher(None, want, key, autojunk=False).ratio()
        if key not in scored or ratio > scored[key][0]:
            scored[key] = (ratio, cand)
    ranked = sorted(scored.values(), reverse=True)
    if not ranked or ranked[0][0] < cutoff:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < MATCH_MARGIN:
        log.info("[vocabulary] %r is as close to %r as to %r; not guessing", phrase, ranked[0][1], ranked[1][1])
        return None
    return ranked[0][1]


def correct(phrase: str, *kinds: str, candidates: Iterable[str] | None = None) -> str | None:
    """``best_match`` against the kept names of these kinds (plus ``candidates``), logged when it corrects."""
    pool = list(candidates or []) + names(*kinds)
    hit = best_match(phrase, pool)
    if hit and squash(hit) != squash(phrase):
        log.info("[vocabulary] heard %r, searching %r instead", phrase, hit)
    return hit


def reset() -> None:
    """Forget every name (tests)."""
    with _lock:
        for bucket in _names.values():
            bucket.clear()
        _refreshed.clear()
