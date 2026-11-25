"""Find what "Play X" names in every media source the household has set up, and play it.

"Play <title>" used to be a Music Assistant music search (tracks, artists, albums,
playlists, radio) and nothing else, so a children's audiobook the household owns was
"No match". This module asks every source that is set up and working right now, ranks
what comes back, and plays the best match, asks a short question when two kinds of
match are equally good, or says which sources it searched when nothing matched.

Where the sources come from. Nothing here names a household's server, player or
title. A source is searched only when its integration is configured and usable now:

* ``music_assistant``: the hub's Music Assistant config entry is loaded (it is
  skipped, and said, when the entry is disabled or not loaded), or the person's own
  Music Assistant server from Identity (Accounts & Integrations) when there is no
  hub entry. Music Assistant covers tracks, artists, albums, playlists, radio,
  podcasts and audiobooks, whatever providers it has.
* ``audiobookshelf``: the person's (or the household's) Audiobookshelf login from
  Identity. Audiobooks, series, authors and podcasts.
* ``calibre``: the ``calibre_library_path`` setting and a Nextcloud login. Books
  that can be read aloud, offered when nothing playable matched.
* ``web_video``: YouTube through yt-dlp, which execution has built in. Searched only
  when the target can show video (a screen in the hub's capability index) or the
  request asks for a video, and never when the ``voice_video_search`` setting is
  "off". Always the last resort, always with safe search, and a result plays only when
  it is suitable and a strong match for what was asked; a weaker one is asked about
  ("I found 'X' on YouTube. Play it?"). See video_safety.py.

Each kind of integration needs an adapter here (how to search it, how to play its
items); adding, removing, disabling or breaking a configured instance changes what
is searched with no code change, and a source that fails is reported as skipped.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from services.common.media_titles import (
    artists_match,
    clean_title,
    main_title,
    normalize,
    sounds_like,
    tidy_by,
    title_score,
)
from services.execution.schemas import AudiobookshelfRequest, CalibreRequest, ExecutionResult, MediaPlayRequest

log = logging.getLogger("execution.media_finder")

SERVICE = "media_play"

#: Seconds each source gets to answer a search. A voice request waits for the slowest.
SOURCE_TIMEOUT_S = 12.0
#: A match scoring at least this is played; below it the request found nothing.
PLAY_THRESHOLD = 0.7
#: Two kinds of match both at or above this, within ``ASK_MARGIN`` of each other, are
#: an honest tie: ask which one was meant instead of guessing.
ASK_FLOOR = 0.9
ASK_MARGIN = 0.05
#: The Identity setting that turns the built-in YouTube search off ("off"); anything else is on.
VIDEO_SEARCH_SETTING = "voice_video_search"

#: Which kind of media each item kind is, for ranking and for the question.
GROUP_OF = {
    "track": "music", "album": "music", "artist": "music", "playlist": "music", "radio": "music",
    "genre": "music",
    "podcast": "podcast", "episode": "podcast", "podcast_episode": "podcast",
    "audiobook": "audiobook", "series": "audiobook", "author": "audiobook",
    "video": "video",
    "book": "book",
}
#: Within music, which kind wins a tie (an artist named exactly beats a track of the same name).
_MUSIC_ORDER = ("artist", "playlist", "album", "track", "radio", "genre")
#: For the same audiobook or podcast found in two sources, the source that plays it with
#: progress, chapters and resume (the Music Assistant copy is the same title through its
#: Audiobookshelf provider).
_SOURCE_PREFERENCE = {"audiobook": ("audiobookshelf", "music_assistant"),
                      "podcast": ("audiobookshelf", "music_assistant")}

#: How each kind is named in a spoken question or answer.
SPOKEN_KIND = {
    "track": "the song", "album": "the album", "artist": "the artist", "playlist": "the playlist",
    "radio": "the radio station", "genre": "the music", "podcast": "the podcast",
    "episode": "the podcast episode", "podcast_episode": "the podcast episode",
    "audiobook": "the audiobook", "series": "the audiobook series", "author": "the audiobooks",
    "video": "the video", "book": "the book",
}
#: Words a follow-up answer may use for each kind ("the audiobook", "the song", "music").
CHOICE_WORDS = {
    "music": ("song", "music", "track", "album", "artist", "playlist", "radio", "station"),
    "audiobook": ("audiobook", "audio book", "book", "series", "story"),
    "podcast": ("podcast", "episode", "show"),
    "video": ("video", "movie", "film", "youtube", "show", "watch"),
    "book": ("book", "read", "yes"),
}

# --------------------------------------------------------------------------- #
# What was asked for: the words, and the kind they name                        #
# --------------------------------------------------------------------------- #

#: Words that name the kind of media and are not part of the title, with the kind they mean.
_KIND_WORDS: tuple[tuple[str, str, str | None], ...] = (
    (r"audio\s*-?\s*books?", "audiobook", None),
    (r"podcasts?", "podcast", None),
    (r"(?:the\s+)?(?:latest\s+|newest\s+|last\s+)?episodes?", "podcast", None),
    (r"songs?|tracks?|tunes?", "music", "track"),
    (r"albums?|records?", "music", "album"),
    (r"playlists?", "music", "playlist"),
    (r"radio(?:\s+station)?|stations?", "music", "radio"),
    (r"music", "music", None),
    (r"movies?|films?|videos?|youtube(?:\s+videos?)?|clips?|trailers?|cartoons?|tv\s+shows?|shows?", "video", None),
)
_KIND_HEAD = re.compile(
    r"^(?:the\s+|an?\s+|some\s+)?(?:" + "|".join(p for p, _g, _k in _KIND_WORDS) + r")\s+(?:of\s+|called\s+|named\s+)?",
    re.IGNORECASE)
_KIND_TAIL = re.compile(
    r"\s+(?:the\s+|an?\s+)?(?:" + "|".join(p for p, _g, _k in _KIND_WORDS) + r")$", re.IGNORECASE)
_ON_YOUTUBE = re.compile(r"\s+(?:on|from)\s+youtube$", re.IGNORECASE)


def _kind_of(words: str) -> tuple[str | None, str | None]:
    for pattern, group, kind in _KIND_WORDS:
        if re.fullmatch(rf"(?:the\s+|an?\s+|some\s+)?(?:latest\s+|newest\s+|last\s+)?(?:{pattern})", words.strip(),
                        re.IGNORECASE):
            return group, kind
    return None, None


def split_kind(query: str, hint: str | None = None) -> tuple[str, str | None, str | None]:
    """(title words, group, kind) from "the audiobook Moon Garden", "Harbor the song", "Pancake Robots on YouTube".

    ``hint`` is a media type the caller already knows ("video", "podcast", "audiobook",
    "music"); words in the query win over it.
    """
    text = (query or "").strip(" ,.!?\"'")
    group: str | None = None
    kind: str | None = None
    if _ON_YOUTUBE.search(text):
        text, group = _ON_YOUTUBE.sub("", text).strip(), "video"
    m = _KIND_HEAD.match(text)
    if m and text[m.end():].strip():
        g, k = _kind_of(re.sub(r"\s+(?:of|called|named)$", "", m.group(0).strip(), flags=re.IGNORECASE))
        group, kind = group or g, k
        text = text[m.end():].strip()
    else:
        m = _KIND_TAIL.search(text)
        if m and text[: m.start()].strip():
            g, k = _kind_of(m.group(0).strip())
            group, kind = group or g, k
            text = text[: m.start()].strip()
    if group is None and hint:
        group = {"video": "video", "podcast": "podcast", "audiobook": "audiobook", "music": "music",
                 "radio": "music"}.get(hint)
    return text.strip(" ,.!?\"'"), group, kind


def search_words(text: str) -> str:
    """The words to search with: a leading "a", "an" or "the" dropped ("a Pancake Robots" -> "Pancake Robots").

    Titles are compared without it (``normalize``), but a source's own search is literal.
    """
    words = (text or "").split()
    return " ".join(words[1:]) if len(words) > 1 and words[0].lower() in ("a", "an", "the") else (text or "")


_BY = re.compile(r"^(?P<what>.+?)\s+by\s+(?P<who>.+)$", re.IGNORECASE)


def split_by(text: str) -> tuple[str, str]:
    """("Harbor", "The Example Band") from "Harbor by The Example Band"; ("x", "") without a "by".

    "by the artist, X" and "by the band X" are "by X".
    """
    text = tidy_by(text)
    m = _BY.match(text or "")
    if m and m.group("what").strip() and m.group("who").strip():
        return m.group("what").strip(), m.group("who").strip()
    return (text or "").strip(), ""


# --------------------------------------------------------------------------- #
# How well a title matches                                                     #
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Candidates and the decision                                                  #
# --------------------------------------------------------------------------- #

@dataclass
class Candidate:
    """One thing a source could play (or read)."""

    source: str           # the adapter: music_assistant | audiobookshelf | calibre | web_video
    kind: str             # track, album, artist, playlist, radio, podcast, episode, audiobook, series, author, video, book
    title: str
    ident: str            # what the source plays it by: a URI, an item id, a URL
    by: str = ""          # artist, author or channel
    score: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def group(self) -> str:
        return GROUP_OF.get(self.kind, "music")

    def spoken(self) -> str:
        """"Moon Garden, the audiobook" for an answer."""
        name = clean_title(self.title)
        if self.kind == "author":
            return f"the audiobooks by {name}"
        return f"{name}, {SPOKEN_KIND.get(self.kind, 'the ' + self.kind)}"

    def as_pick(self) -> dict[str, Any]:
        return asdict(self)


#: A title of one word this short ("Up", "Go", "Home") is too easy to find inside other titles:
#: only the title itself counts, never "Rise Up" or "Going Home".
SHORT_TITLE_CHARS = 4


def is_short_title(query: str) -> bool:
    words = normalize(query).split()
    return len(words) == 1 and len(words[0]) <= SHORT_TITLE_CHARS


def score_candidates(cands: list[Candidate], query: str, by: str = "") -> list[Candidate]:
    """Each candidate scored against ``query`` (and ``by``, the artist or author said), best first.

    The artist or author counts when it is the same name or sounds like it ("Return by the
    Great Evens" is the song by The Gray Havens), and one of several credited is enough
    ("Up by Example Singer" is the song by "Example Singer & Other Voice").
    """
    short = is_short_title(query)
    for c in cands:
        cap = {"series": 0.97, "author": 0.96}.get(c.kind, 1.0)
        c.score = title_score(query, c.title, cap=cap)
        if short and c.kind not in ("artist", "author") and normalize(query) != normalize(main_title(c.title)):
            c.score = min(c.score, PLAY_THRESHOLD - 0.1)
        if by:
            if c.by and artists_match(by, c.by):
                c.score = min(1.0, c.score + 0.05)
            elif c.kind not in ("artist", "author"):
                c.score *= 0.6  # "Harbor by The Example Band" is not the audiobook Harbor
    return sorted(cands, key=_order)


def _order(c: Candidate) -> tuple:
    music_rank = _MUSIC_ORDER.index(c.kind) if c.kind in _MUSIC_ORDER else len(_MUSIC_ORDER)
    return (-c.score, music_rank, c.title.lower())


def dedupe(cands: list[Candidate]) -> list[Candidate]:
    """One copy of an audiobook or podcast found in two sources: the source that plays it best."""
    kept: dict[tuple[str, str], Candidate] = {}
    out: list[Candidate] = []
    for c in cands:
        prefs = _SOURCE_PREFERENCE.get(c.group)
        if not prefs or c.kind in ("series", "author"):
            out.append(c)
            continue
        key = (c.group, normalize(main_title(c.title)))
        have = kept.get(key)
        if have is None:
            kept[key] = c
            out.append(c)
            continue
        rank = {s: i for i, s in enumerate(prefs)}
        if rank.get(c.source, 99) < rank.get(have.source, 99):
            out[out.index(have)] = c
            kept[key] = c
    return out


@dataclass
class Decision:
    action: str                       # "play" | "ask" | "none"
    best: Candidate | None = None
    options: list[Candidate] = field(default_factory=list)


def decide(ranked: list[Candidate], group: str | None = None) -> Decision:
    """Play the best match, ask between two kinds that tie, or say nothing matched.

    ``group`` (a kind the request named: "the audiobook ...") narrows the choice to that
    kind when it has a match; otherwise every kind competes.
    """
    pool = [c for c in ranked if c.score >= PLAY_THRESHOLD]
    if group:
        named = [c for c in pool if c.group == group]
        pool = named or ([] if group == "video" else pool)
    if not pool:
        return Decision("none")
    best = pool[0]
    if not group:
        other = next((c for c in pool if c.group != best.group), None)
        if other and best.score >= ASK_FLOOR and other.score >= ASK_FLOOR and best.score - other.score <= ASK_MARGIN:
            return Decision("ask", best, [best, other])
    return Decision("play", best)


def question(query: str, options: list[Candidate]) -> str:
    """"Harbor: the song, or the audiobook?" naming each option once."""
    labels = []
    for c in options:
        label = SPOKEN_KIND.get(c.kind, "the " + c.kind)
        if c.kind == "track" and c.by:
            label = f"the song by {c.by}"
        if label not in labels:
            labels.append(label)
    return f"{clean_title(query)}: {', '.join(labels[:-1])}, or {labels[-1]}?"


def choice_words(c: Candidate) -> list[str]:
    words = list(CHOICE_WORDS.get(c.group, ()))
    if c.by:
        words.append(c.by.lower())
    return words


def searched_phrase(labels: list[str]) -> str:
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " and " + labels[-1]


# --------------------------------------------------------------------------- #
# Which sources are set up                                                     #
# --------------------------------------------------------------------------- #

@dataclass
class Source:
    key: str
    label: str
    ready: bool
    why: str = ""
    route: str = ""         # music_assistant: "hub" or "direct"
    entry_id: str = ""      # music_assistant via the hub: its config entry


async def _hub_entries(ctx, domain: str) -> list[dict]:
    from services.execution import ha_client, ha_health

    if not getattr(ctx, "ha_url", None) or not getattr(ctx, "ha_token", None) or ha_health.is_down(ctx.ha_url):
        return []
    try:
        entries = await ha_client.get_config_entries(ctx.ha_url, ctx.ha_token, domain)
    except Exception as exc:
        log.warning(f"[media/find] could not read the hub's {domain} entries: {exc}")
        return []
    return [e for e in entries or [] if isinstance(e, dict) and e.get("domain") == domain]


async def _setting(key: str) -> str:
    from services.execution.handlers import media as media_handler

    return await media_handler._global_setting(key)


async def music_assistant_source(ctx) -> Source | None:
    """The hub's Music Assistant entry when there is one (loaded or not), else the person's own server."""
    entries = await _hub_entries(ctx, "music_assistant")
    if entries:
        loaded = [e for e in entries if e.get("state") == "loaded" and not e.get("disabled_by")]
        label = str((loaded or entries)[0].get("title") or "Music Assistant")
        if loaded:
            return Source("music_assistant", label, True, route="hub", entry_id=str(loaded[0].get("entry_id") or ""))
        e = entries[0]
        why = "it is turned off" if e.get("disabled_by") else f"it is {str(e.get('state') or 'not loaded').replace('_', ' ')}"
        return Source("music_assistant", label, False, why=why)
    if getattr(ctx, "mass_url", None) and getattr(ctx, "mass_token", None):
        return Source("music_assistant", "Music Assistant", True, route="direct")
    if getattr(ctx, "mass_error", None):
        return Source("music_assistant", "Music Assistant", False, why=str(ctx.mass_error))
    return None


def audiobookshelf_source(ctx) -> Source | None:
    from services.execution import abs_client

    url, key, user, password = abs_client.resolve_abs_credentials(ctx)
    if not url:
        return None
    if not (key or (user and password)):
        return Source("audiobookshelf", "Audiobookshelf", False, why="it has no login")
    return Source("audiobookshelf", "Audiobookshelf", True)


async def calibre_source(ctx) -> Source | None:
    path = (await _setting("calibre_library_path")).strip()
    if not path:
        return None
    if not getattr(ctx, "nextcloud_url", None):
        return Source("calibre", "the book library", False, why="there is no Nextcloud login to read it with")
    return Source("calibre", "the book library", True)


async def web_video_source(*, screen: bool, wants_video: bool) -> Source | None:
    if (await _setting(VIDEO_SEARCH_SETTING)).strip().lower() == "off":
        return None
    if not (screen or wants_video):
        return None
    return Source("web_video", "YouTube", True)


async def discover_sources(ctx, *, screen: bool, wants_video: bool) -> list[Source]:
    """Every media source set up for this person, ready or not (with why not)."""
    found = await asyncio.gather(
        music_assistant_source(ctx), calibre_source(ctx), web_video_source(screen=screen, wants_video=wants_video),
        return_exceptions=True,
    )
    sources: list[Source] = []
    ma, calibre, video = found
    abs_src = audiobookshelf_source(ctx)
    for s in (ma, abs_src, video, calibre):
        if isinstance(s, BaseException):
            log.warning(f"[media/find] could not check a media source: {s}")
        elif s is not None:
            sources.append(s)
    return sources


# --------------------------------------------------------------------------- #
# Searching each source                                                        #
# --------------------------------------------------------------------------- #

_MA_TYPES = ["track", "artist", "album", "playlist", "radio", "audiobook", "podcast"]
_MA_CATEGORY = {"tracks": "track", "artists": "artist", "albums": "album", "playlists": "playlist",
                "radio": "radio", "radios": "radio", "audiobooks": "audiobook", "podcasts": "podcast",
                "genres": "genre", "podcast_episodes": "episode", "episodes": "episode"}


def _flatten_ma(resp: dict) -> list[Candidate]:
    out: list[Candidate] = []
    for category, items in (resp or {}).items():
        if not isinstance(items, list):
            continue
        kind = _MA_CATEGORY.get(category, category)
        for item in items:
            if not isinstance(item, dict) or not item.get("uri"):
                continue
            artists = item.get("artists") or item.get("authors") or []
            by = " & ".join(n for n in ((a.get("name", "") if isinstance(a, dict) else str(a or ""))
                                        for a in artists) if n)
            out.append(Candidate("music_assistant", str(item.get("media_type") or kind), str(item.get("name") or ""),
                                 str(item["uri"]), by=by or str(item.get("artist") or "")))
    return out


#: Music Assistant library kinds small enough to match locally as well as through MA's search,
#: so a name heard wrong ("er seven" for a station "ARE7") is still found by how it sounds.
_MA_LOCAL_KINDS = ("playlist", "radio")
MA_LIBRARY_TTL_S = 600.0
_ma_library_cache: dict[str, tuple[float, list[Candidate]]] = {}


async def _ma_library_names(ctx, source: Source) -> list[Candidate]:
    """The library's playlists and radio stations (cached ``MA_LIBRARY_TTL_S``), through the hub."""
    import time

    from services.execution.handlers import mass_ha_client

    if source.route != "hub":
        return []
    key = f"{ctx.ha_url}|{source.entry_id}"
    cached = _ma_library_cache.get(key)
    if cached and time.monotonic() - cached[0] < MA_LIBRARY_TTL_S:
        return cached[1]
    found: list[Candidate] = []
    for kind in _MA_LOCAL_KINDS:
        items = await mass_ha_client.get_library(ctx.ha_url, ctx.ha_token, kind, limit=500,
                                                 mass_entry_id=source.entry_id)
        found += [Candidate("music_assistant", kind, str(i.get("name") or ""), str(i["uri"]))
                  for i in items or [] if i.get("uri") and i.get("name")]
    _ma_library_cache[key] = (time.monotonic(), found)
    return found


async def search_music_assistant(ctx, source: Source, query: str) -> list[Candidate]:
    """MA's own search, plus the library's playlists and stations whose names match ``query``."""
    found = await _search_music_assistant(ctx, source, query)
    try:
        local = await _ma_library_names(ctx, source)
    except Exception as exc:  # the search above is the answer; this only adds to it
        log.warning(f"[media/find] could not list Music Assistant's playlists and stations: {exc}")
        local = []
    have = {c.ident for c in found}
    found += [Candidate(**c.as_pick()) for c in local  # copies: scoring writes to them
              if c.ident not in have and title_score(query, c.title) >= _CATALOGUE_FLOOR]
    return found


async def _search_music_assistant(ctx, source: Source, query: str) -> list[Candidate]:
    from services.common import ma_identity
    from services.execution import ha_client

    if source.route == "hub":
        result = await ha_client.call_service(
            ctx.ha_url, ctx.ha_token, "music_assistant", "search", entity_id="",
            service_data={"config_entry_id": source.entry_id, "name": query, "media_type": _MA_TYPES, "limit": 8,
                          **ma_identity.act_as(ctx)},
            return_response=True,
        )
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "search failed"))
        raw = result.get("service_response") or {}
        return _flatten_ma(raw.get("service_response", raw) if isinstance(raw, dict) else {})
    from services.execution.handlers import mass_client

    items = await mass_client.search(ctx.mass_url, ctx.mass_token, query, limit=60, media_types=_MA_TYPES,
                                     library_only=False)
    return [Candidate("music_assistant", str(i.get("type") or "track"), str(i.get("name") or ""), str(i["uri"]),
                      by=" & ".join(i.get("artists") or []) or str(i.get("artist") or ""))
            for i in items if i.get("uri")]


#: The library kinds searched by name when Music Assistant's own search finds nothing.
_MA_LIBRARY_KINDS = ("track", "album")


async def search_ma_library(ctx, source: Source, query: str, kinds: tuple[str, ...] = _MA_LIBRARY_KINDS
                            ) -> list[Candidate]:
    """The library's tracks and albums whose names match ``query``, with every credited artist.

    Music Assistant's ``search`` misses very short titles ("Up") that its library lists, so
    this asks the library itself (``library_items`` with the words as its filter).
    """
    from services.execution.handlers import mass_client, mass_ha_client

    out: list[Candidate] = []
    for kind in kinds:
        if source.route == "hub":
            items = await mass_ha_client.get_library(ctx.ha_url, ctx.ha_token, kind, limit=50, search=query,
                                                     mass_entry_id=source.entry_id)
        else:
            items = await mass_client.library_items(ctx.mass_url, ctx.mass_token, f"{kind}s", query, limit=50)
        out += [Candidate("music_assistant", kind, str(i.get("name") or ""), str(i["uri"]),
                          by=" & ".join(_artist_names(i)))
                for i in items or [] if isinstance(i, dict) and i.get("uri") and i.get("name")]
    return out


def _artist_names(item: dict) -> list[str]:
    """Every artist credited on a library item, whether listed as names or as artist items."""
    names = (a.get("name") if isinstance(a, dict) else a for a in item.get("artists") or [])
    return [str(n) for n in names if n]


_SERIES_NUMBER = re.compile(r"\s*#\s*[\w.\-]+\s*$")
#: How long an Audiobookshelf catalogue is kept between searches.
ABS_CATALOGUE_TTL_S = 600.0
_abs_catalogues: dict[str, tuple[float, list[dict]]] = {}
#: A catalogue entry is a candidate at or above this score (the decision applies its own bar).
_CATALOGUE_FLOOR = 0.5


def _series_names(meta: dict) -> list[str]:
    """"Moon Garden" from "Moon Garden #1"; several when an item is in several series."""
    names = []
    raw = meta.get("seriesName") or ""
    parts = re.split(r",\s*(?=[^,]*#)", raw) if "#" in raw else ([raw] if raw else [])
    for part in parts:
        name = _SERIES_NUMBER.sub("", part).strip()
        if name and name not in names:
            names.append(name)
    for entry in meta.get("series") or []:
        name = str(entry.get("name") or "").strip() if isinstance(entry, dict) else ""
        if name and name not in names:
            names.append(name)
    return names


async def _abs_login(ctx) -> tuple[str, str]:
    from services.execution import abs_client

    url, key, user, password = abs_client.resolve_abs_credentials(ctx)
    if user and password:
        key = await abs_client.abs_login(url, user, password) or key
    if not url or not key:
        raise RuntimeError("no Audiobookshelf login")
    return url, key


async def abs_catalogue(ctx) -> list[dict]:
    """Every playable item in every Audiobookshelf library: id, kind, title, author, series, added (ms)."""
    import time

    from services.execution import abs_client
    from services.execution.handlers.audiobookshelf import _is_unplayable_abs_book

    url, key = await _abs_login(ctx)
    cached = _abs_catalogues.get(url)
    if cached and time.monotonic() - cached[0] < ABS_CATALOGUE_TTL_S:
        return cached[1]
    libs = await abs_client.get_libraries(url, key)
    if "error" in libs:
        raise RuntimeError(str(libs["error"]))
    items: list[dict] = []
    for lib in libs.get("libraries") or []:
        if not lib.get("id"):
            continue
        podcast = "podcast" in str(lib.get("mediaType") or "").lower()
        listed = await abs_client.get_all_library_items(url, key, lib["id"])
        if isinstance(listed, dict):
            raise RuntimeError(str(listed.get("error") or "listing failed"))
        for item in listed:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if not podcast and _is_unplayable_abs_book(item):
                continue
            meta = (item.get("media") or {}).get("metadata") or {}
            items.append({"id": item["id"], "kind": "podcast" if podcast else "audiobook",
                          "title": str(meta.get("title") or ""),
                          "author": str(meta.get("authorName") or meta.get("author") or ""),
                          "series": [] if podcast else _series_names(meta),
                          "added": item.get("addedAt") or 0})
    _abs_catalogues[url] = (time.monotonic(), items)
    return items


async def search_audiobookshelf(ctx, query: str) -> list[Candidate]:
    """Audiobooks, series, authors and podcasts whose names match ``query``, by the shared title rules.

    Matched against the library's own catalogue rather than Audiobookshelf's search, which
    is literal: "Sunny B Days" does not find "Sunny B. Days", nor "first chronicle" "1 Chronicle".
    """
    out: list[Candidate] = []
    series: dict[str, str] = {}
    authors: dict[str, str] = {}
    for item in await abs_catalogue(ctx):
        if title_score(query, item["title"]) >= _CATALOGUE_FLOOR:
            out.append(Candidate("audiobookshelf", item["kind"], item["title"], item["id"], by=item["author"]))
        for name in item["series"]:
            if title_score(query, name) >= _CATALOGUE_FLOOR:
                series.setdefault(normalize(name), name)
        for name in re.split(r",\s*", item["author"]) if item["kind"] == "audiobook" else []:
            if name and title_score(query, name) >= ASK_FLOOR:
                authors.setdefault(normalize(name), name)
    out += [Candidate("audiobookshelf", "series", n, n) for n in series.values()]
    out += [Candidate("audiobookshelf", "author", n, n) for n in authors.values()]
    return out


async def search_calibre(ctx, query: str) -> list[Candidate]:
    from services.execution.handlers import calibre as calibre_handler

    res = await calibre_handler.handle_calibre(CalibreRequest(action="search", query=query, limit=10,
                                                              user_context=ctx))
    if res.status != "SUCCESS":
        raise RuntimeError(res.message)
    out = []
    for b in (res.detail or {}).get("books") or []:
        authors = b.get("authors")
        by = str(b.get("author") or (" & ".join(authors) if isinstance(authors, list) else authors) or "")
        out.append(Candidate("calibre", "book", str(b.get("title") or ""), str(b.get("book_id") or ""), by=by))
    return [c for c in out if c.title and c.ident]


async def search_web_video(query: str, ctx=None) -> list[Candidate]:
    """YouTube's first few results for ``query``, searched at the request's safe-search level."""
    from services.execution import video_safety
    from services.execution.handlers import video as video_handler

    level = await video_safety.safe_search_level(ctx)
    found = await video_handler.search_youtube_entries(query, level)
    return [Candidate("web_video", "video", e.get("title") or query, e["url"], by=str(e.get("channel") or ""),
                      extra={"title_known": bool(e.get("title")), "age_limit": e.get("age_limit"),
                             "safe_search": level})
            for e in found if e.get("url")]


#: At most this many results are checked with the site before giving up (each check is a request).
VIDEO_CHECKS = 3
#: What a "yes" to "Play it?" may be said as (one option: any yes works too, fast_media.match_choice).
_PLAY_IT_WORDS = ["yes", "yeah", "yep", "sure", "please", "okay", "ok", "play it", "go ahead"]


async def choose_video(vids: list[Candidate], what: str, ctx) -> tuple[str, Candidate | None, str]:
    """("play" | "ask" | "none", the video, why): the best suitable match among YouTube's results.

    Results whose title or channel is on the blocklist, or whose rating a shared screen or a
    child may not see, are skipped; the rest are ranked by how well they match ``what``. A
    strong match plays; anything weaker is offered with a question, never played unasked.
    """
    from services.execution import video_safety

    words = await video_safety.blocklist()
    ranked = sorted(vids, key=lambda c: -video_safety.match_score(what, c.title if c.extra.get("title_known", True) else "",
                                                                  c.by))
    reasons: list[str] = []
    checks = 0
    for c in ranked:
        if video_safety.blocked_by(c.title, c.by, words):
            reasons.append("blocked")
            continue
        if c.extra.get("age_limit") is not None and video_safety.age_refused(c.extra["age_limit"], ctx):
            reasons.append("age_restricted")
            continue
        if checks >= VIDEO_CHECKS:
            break
        checks += 1
        verdict = await video_safety.screen_url(c.ident, ctx, words=words)
        if not verdict.ok:
            reasons.append(verdict.why)
            continue
        if verdict.title:
            c.title, c.extra["title_known"] = verdict.title, True
        if verdict.channel:
            c.by = verdict.channel
        c.extra["age_limit"] = verdict.age_limit
        c.score = video_safety.match_score(what, c.title if c.extra.get("title_known", True) else "", c.by)
        if c.score >= video_safety.STRONG_MATCH:
            return "play", c, "strong_match"
        return "ask", c, "weak_match"
    return "none", None, ",".join(dict.fromkeys(reasons)) or "no_results"


async def offer_or_play_video(vids: list[Candidate], what: str, req: MediaPlayRequest, entity_id: str, ctx,
                              player: str) -> ExecutionResult:
    """Play a strong, suitable YouTube match; ask about a weaker one; say so when none is suitable."""
    from services.execution import video_safety

    level = str((vids[0].extra if vids else {}).get("safe_search") or video_safety.DEFAULT_LEVEL)
    action, cand, why = await choose_video(vids, what, ctx)
    video_safety.log_decision(what, cand.title if cand else "", cand.score if cand else 0.0, level, action, why)
    if action == "play" and cand is not None:
        return await play_candidate(cand, req, entity_id, ctx, player)
    if action == "ask" and cand is not None:
        shown = clean_title(cand.title) if cand.extra.get("title_known", True) else "a video"
        return ExecutionResult.ok(
            f"I found '{shown}' on YouTube. Play it?", service=SERVICE,
            detail={"route": "ask", "reason": why, "choice": {
                "query": what, "entity_id": entity_id,
                "options": [{"label": "play it", "words": _PLAY_IT_WORDS, "pick": cand.as_pick()}]}})
    name = clean_title(what) or "that"
    return ExecutionResult.fail(
        f"I didn't find a video of {name} on YouTube that's suitable to play here.", service=SERVICE,
        detail={"route": "none", "reason": why, "searched": ["YouTube"]})


async def find_video_and_play(req: MediaPlayRequest, query: str, entity_id: str, ctx,
                              player: str | None = None) -> ExecutionResult:
    """Search YouTube for ``query`` and play (strong match), ask (weak match) or refuse (unsuitable)."""
    vids = await search_web_video(search_words(query), ctx)
    if not vids:
        return ExecutionResult.fail(f"Could not find video for '{query}'.", service=SERVICE,
                                    detail={"route": "none", "searched": ["YouTube"]})
    return await offer_or_play_video(vids, query, req, entity_id, ctx, player or entity_id)


async def _search(ctx, source: Source, query: str) -> list[Candidate]:
    if source.key == "music_assistant":
        return await search_music_assistant(ctx, source, query)
    if source.key == "audiobookshelf":
        return await search_audiobookshelf(ctx, query)
    if source.key == "calibre":
        return await search_calibre(ctx, query)
    if source.key == "web_video":
        return await search_web_video(query, ctx)
    return []


async def search_sources(ctx, sources: list[Source], query: str) -> tuple[list[Candidate], list[str], list[str]]:
    """(candidates, labels searched, "<label> (why)" skipped) over the ready ``sources`` at once."""
    ready = [s for s in sources if s.ready]
    skipped = [f"{s.label} ({s.why})" for s in sources if not s.ready]
    results = await asyncio.gather(
        *(asyncio.wait_for(_search(ctx, s, query), SOURCE_TIMEOUT_S) for s in ready), return_exceptions=True)
    cands: list[Candidate] = []
    searched: list[str] = []
    for s, r in zip(ready, results, strict=True):
        if isinstance(r, BaseException):
            why = "it did not answer in time" if isinstance(r, TimeoutError) else "it did not answer"
            log.warning(f"[media/find] {s.key} search for {query!r} failed: {r!r}")
            skipped.append(f"{s.label} ({why})")
            continue
        searched.append(s.label)
        log.info(f"[media/find] {s.key}: {len(r)} result(s) for {query!r}")
        cands.extend(r)
    return cands, searched, skipped


# --------------------------------------------------------------------------- #
# The whole request                                                            #
# --------------------------------------------------------------------------- #

async def target_facts(ctx, entity_id: str) -> tuple[bool, str]:
    """(the player shows video, its spoken name), from the hub's capability index and states."""
    from services.execution import ha_client
    from services.execution.handlers import media as media_handler

    screen = False
    name = entity_id
    try:
        state = await ha_client.get_state(ctx.ha_url, ctx.ha_token, entity_id) if ctx.ha_url else None
        attrs = (state or {}).get("attributes") or {}
        name = str(attrs.get("friendly_name") or entity_id)
        idx = await media_handler._routing_index(ctx.ha_url, ctx.ha_token, "video target check")
        in_index = idx is not None and entity_id in idx
        screen = idx.is_screen(entity_id) if in_index and idx is not None else \
            str(attrs.get("device_class") or "").lower() == "tv"
    except Exception as exc:
        log.warning(f"[media/find] could not tell whether {entity_id} shows video: {exc}")
    return screen, name


async def screens_named(ctx, limit: int = 3) -> list[str]:
    """Names of a few players that show video, to say where a video can play."""
    from services.execution import ha_client
    from services.execution.handlers import media as media_handler

    try:
        idx = await media_handler._routing_index(ctx.ha_url, ctx.ha_token, "screen list")
        if idx is None:
            return []
        states = {s.get("entity_id"): s for s in await ha_client.get_states(ctx.ha_url, ctx.ha_token) or []}
        names: list[str] = []
        for eid in idx.media_targets(available_only=True):
            if idx.is_screen(eid):
                n = str(((states.get(eid) or {}).get("attributes") or {}).get("friendly_name") or "")
                if n and n not in names:
                    names.append(n)
        return names[:limit]
    except Exception as exc:
        log.warning(f"[media/find] could not list the screens: {exc}")
        return []


def _rank(cands: list[Candidate], what: str, by: str, title: str) -> tuple[list[Candidate], str, str]:
    """(ranked, title, artist): scored as "<what> by <by>", or as the words said when that scores better."""
    ranked = score_candidates(dedupe(cands), what, by)
    if by:
        # Scored on copies: scoring writes each candidate's score, and the "by" scores above must stay.
        whole = score_candidates([Candidate(**c.as_pick()) for c in dedupe(cands)], title)
        if whole and (not ranked or whole[0].score > ranked[0].score):
            return whole, title, ""
    return ranked, what, by


def _playable(ranked: list[Candidate]) -> bool:
    return any(c.score >= PLAY_THRESHOLD for c in ranked)


def title_artist_guesses(text: str, max_words: int = 8) -> list[tuple[str, str]]:
    """(title, artist) for every way to cut "Up Example Singer" or "Example Singer Up" in two.

    Said without "by", the artist may come before or after the title; the library decides
    which cut is real (``search_artist_tracks``).
    """
    words = (text or "").split()
    if not 2 <= len(words) <= max_words:
        return []
    out: list[tuple[str, str]] = []
    for i in range(1, len(words)):
        left, right = " ".join(words[:i]), " ".join(words[i:])
        out += [(left, right), (right, left)]
    return out


def _library_words(title: str) -> list[str]:
    """What to filter the library's names by: the title, and its two longest words when it has several.

    The library filters by the letters as written, so "Dont Stop" does not list "Don't Stop!";
    "Stop" does, and the title rules (punctuation never matters) then match it.
    """
    words = search_words(title)
    parts = re.findall(r"[A-Za-z0-9]+", words)
    if len(parts) < 2:
        return [words]
    longest = sorted(dict.fromkeys(p for p in parts if len(p) >= 4), key=len, reverse=True)[:2]
    return [words, *longest]


async def search_artist_tracks(ctx, library: list[Source], pairs: list[tuple[str, str]]) -> list[Candidate]:
    """Library tracks named each pair's title AND credited to its artist, scored as that pair.

    The way to find a short title ("Up") by an artist: Music Assistant's global search misses
    it, but the library's tracks named "up" include it, credited to that artist.
    """
    ma = [s for s in library if s.key == "music_assistant" and s.ready]
    if not ma or not pairs:
        return []
    titles = list(dict.fromkeys(w for t, _a in pairs for w in _library_words(t)))
    results = await asyncio.gather(
        *(asyncio.wait_for(search_ma_library(ctx, ma[0], t, ("track",)), SOURCE_TIMEOUT_S) for t in titles),
        return_exceptions=True)
    by_title: dict[str, list[Candidate]] = {}
    for t, r in zip(titles, results, strict=True):
        if isinstance(r, BaseException):
            log.warning(f"[media/find] library tracks named {t!r} could not be listed: {r!r}")
            continue
        by_title[t] = r
    found: list[Candidate] = []
    for title, artist in pairs:
        listed = {c.ident: c for w in _library_words(title) for c in by_title.get(w, [])}
        for c in listed.values():
            if c.by and artists_match(artist, c.by) and title_score(title, c.title) >= PLAY_THRESHOLD:
                hit = Candidate(**c.as_pick())
                hit.extra = {**hit.extra, "asked_title": title, "asked_by": artist}
                found.append(hit)
    return found


async def collect(ctx, library: list[Source], title: str
                  ) -> tuple[list[Candidate], str, str, list[str], list[str]]:
    """(ranked, title, artist, searched, skipped) for the words ``title`` over the ``library`` sources.

    The sources' own searches first ("<title>", and "<title> <artist>" when an artist was
    named). When nothing found is good enough to play, the library's own lists by name:
    the artist's tracks with that title (said with or without "by"), then tracks and albums
    with the words in their name. Music Assistant's search misses very short titles.
    """
    what, by = split_by(title)
    if by:
        heard_by, by = by, known_name(by)
        if by != heard_by:
            log.info(f"[media/find] heard the artist or author {heard_by!r} as the library's {by!r}")
    if by:
        # "Return by <artist>": a one-word title is common; the title with its artist finds this one.
        (cands, searched, skipped), (more, _s, _k), tracks = await asyncio.gather(
            search_sources(ctx, library, search_words(what)),
            search_sources(ctx, library, f"{search_words(what)} {by}"),
            search_artist_tracks(ctx, library, [(what, by)]))
        cands = cands + more + tracks
    else:
        cands, searched, skipped = await search_sources(ctx, library, search_words(what))
    if by and not any(title_score(what, c.title) >= PLAY_THRESHOLD for c in cands):
        # "X by Y" may be one title ("Murder by Death"): search the words as said too.
        more, _s, _k = await search_sources(ctx, library, search_words(title))
        cands += more
    ranked, what_r, by_r = _rank(cands, what, by, title)
    if _playable(ranked):
        return ranked, what_r, by_r, searched, skipped
    # Nothing to play from the searches: the library's own lists, by name.
    if not by:
        tracks = await search_artist_tracks(ctx, library, title_artist_guesses(search_words(what)))
        if tracks:
            for c in tracks:
                score_candidates([c], c.extra["asked_title"], c.extra["asked_by"])
            best = max(tracks, key=lambda c: c.score)
            guess_title, guess_by = best.extra["asked_title"], best.extra["asked_by"]
            log.info(f"[media/find] {what!r} is {guess_title!r} by {guess_by!r} in the library")
            ranked = score_candidates(dedupe(cands + tracks), guess_title, guess_by)
            if _playable(ranked):
                return ranked, guess_title, guess_by, searched, skipped
    ma = [s for s in library if s.key == "music_assistant" and s.ready]
    if ma:
        try:
            listed = await asyncio.wait_for(search_ma_library(ctx, ma[0], search_words(what)), SOURCE_TIMEOUT_S)
        except Exception as exc:
            log.warning(f"[media/find] the library list for {what!r} failed: {exc!r}")
            listed = []
        if listed:
            log.info(f"[media/find] the library lists {len(listed)} item(s) named like {what!r}")
            ranked, what_r, by_r = _rank(cands + listed, what, by, title)
    return ranked, what_r, by_r, searched, skipped


async def find_and_play(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Search every source for ``req.query`` and play the best match on ``entity_id``."""
    if req.pick:
        return await play_pick(req, entity_id, ctx)
    title, group, kind = split_kind(req.query or "", req.media_type)
    screen, player = await target_facts(ctx, entity_id)
    wants_video = group == "video"
    if wants_video and not screen:
        where = await screens_named(ctx)
        tail = f" Say which one, for example 'on the {where[0]}'." if where else " Name a TV to play it on."
        return ExecutionResult.fail(f"{player} can't show video.{tail}", service=SERVICE,
                                    detail={"route": "none", "reason": "not_a_screen"})
    sources = await discover_sources(ctx, screen=screen, wants_video=wants_video)
    if not sources:
        return ExecutionResult.fail("No music, audiobook or video source is set up yet. Add Music Assistant, "
                                    "Audiobookshelf or the book library in Accounts & Integrations.",
                                    service=SERVICE, detail={"route": "none", "searched": []})
    # The players' libraries first; the book library and the web only when they found nothing to play.
    library = [s for s in sources if s.key not in ("web_video", "calibre")]
    ranked, what, by, searched, skipped = await collect(ctx, library, title)
    if not screen:
        ranked = [c for c in ranked if c.group != "video"]
    log.info(f"[media/find] {what!r} (kind={group or kind or 'any'}, by={by!r}) on {entity_id}: "
             + "; ".join(f"{c.source}:{c.kind}:{c.title!r}={c.score}" for c in ranked[:6]))
    choice = decide(ranked, group)
    if choice.action == "none" and not wants_video:
        better = await corrected_query(ctx, sources, what)
        if better:
            more, _s, _k = await search_sources(ctx, library, better)
            again = score_candidates(dedupe(more), better)
            if decide(again, group).action != "none":
                log.info(f"[media/find] nothing for {what!r}; the library name {better!r} matched")
                what, ranked, choice = better, again, decide(again, group)
    if choice.action == "play" and choice.best is not None and not wants_video:
        return await play_candidate(choice.best, req, entity_id, ctx, player)
    if choice.action == "ask":
        return ExecutionResult.ok(
            question(what, choice.options), service=SERVICE,
            detail={"route": "ask", "choice": {
                "query": what, "entity_id": entity_id,
                "options": [{"label": SPOKEN_KIND.get(c.kind, c.kind), "words": choice_words(c),
                             "pick": c.as_pick()} for c in choice.options]}},
        )
    video_src = next((s for s in sources if s.key == "web_video"), None)
    if video_src:
        # A video was asked for, or a screen found nothing in the libraries: the web's best video.
        vids, v_searched, v_skipped = await search_sources(ctx, [video_src], search_words(what if not by else title))
        searched += v_searched
        skipped += v_skipped
        if vids:
            # Never the first result blindly: a suitable, strong match plays; a weaker one is asked about.
            return await offer_or_play_video(vids, what if not by else title, req, entity_id, ctx, player)
    if wants_video:
        return ExecutionResult.fail(not_found_message(what, searched, skipped), service=SERVICE,
                                    detail={"route": "none", "searched": searched, "skipped": skipped})
    calibre = [s for s in sources if s.key == "calibre"]
    if calibre:
        books, b_searched, b_skipped = await search_sources(ctx, calibre, search_words(what))
        searched += b_searched
        skipped += b_skipped
        books = [c for c in score_candidates(books, what, by) if c.score >= PLAY_THRESHOLD]
        if books:
            book = books[0]
            name = clean_title(book.title)
            return ExecutionResult.ok(
                f"I couldn't find {name} to play, but it's in the book library. Do you want me to read it to you?",
                service=SERVICE,
                detail={"route": "offer_read", "choice": {
                    "query": what, "entity_id": entity_id,
                    "options": [{"label": "read it", "words": ["yes", "yeah", "sure", "please", "read", "okay", "ok"],
                                 "read": {"title": name, "author": book.by, "book_id": book.ident}}]}},
            )
    return ExecutionResult.fail(not_found_message(what, searched, skipped), service=SERVICE,
                                detail={"route": "none", "searched": searched, "skipped": skipped})


def known_name(heard: str) -> str:
    """The library's artist or author ``heard`` sounds like, when exactly one does; else ``heard``.

    From the names the libraries have listed (``media_vocabulary``, kept fresh by
    ``stt_vocabulary``): "the Great Evens" is "The Gray Havens" when that band is in the library.
    """
    from services.execution import media_vocabulary

    names = media_vocabulary.names("artist", "author")
    if not names or any(normalize(heard) == normalize(n) for n in names):
        return heard
    hit = media_vocabulary.best_match(heard, names)
    if hit:
        return hit
    alike = sorted(((sounds_like(heard, n), n) for n in names), reverse=True)
    if alike and alike[0][0] and (len(alike) == 1 or alike[0][0] - alike[1][0] >= media_vocabulary.MATCH_MARGIN):
        return alike[0][1]
    return heard


async def corrected_query(ctx, sources: list[Source], what: str) -> str | None:
    """The library artist or playlist a misheard name most likely was ("brand in Lake"), or None."""
    from services.execution.handlers import media as media_handler

    ma = next((s for s in sources if s.key == "music_assistant" and s.ready and s.route == "hub"), None)
    if ma is None:
        return None
    try:
        return await media_handler._corrected_music_query(ctx, what, ma.entry_id)
    except Exception as exc:
        log.warning(f"[media/find] could not check {what!r} against the library's names: {exc}")
        return None


async def find_only(req: MediaPlayRequest) -> ExecutionResult:
    """The best library match for ``req.query`` and its score, playing nothing.

    Music Assistant and Audiobookshelf only: the web always has some video, and the book
    library is for reading. ``detail``: ``best`` (an item as ``pick`` takes it, or None),
    ``score`` and ``action`` (play, ask or none).
    """
    ctx = req.user_context
    title, group, _kind = split_kind(req.query or "", req.media_type)
    sources = [s for s in await discover_sources(ctx, screen=False, wants_video=False)
               if s.key not in ("web_video", "calibre")]
    ranked, what, _by, searched, skipped = await collect(ctx, sources, title)
    choice = decide(ranked, group)
    best = choice.best
    log.info(f"[media/find] lookup {what!r}: {choice.action}"
             + (f" {best.source}:{best.kind}:{best.title!r}={best.score}" if best else ""))
    return ExecutionResult.ok(
        f"{best.spoken()} ({best.score})" if best else f"Nothing for {what}.", service=SERVICE,
        detail={"best": best.as_pick() if best else None, "score": best.score if best else 0.0,
                "action": choice.action, "searched": searched, "skipped": skipped})


def not_found_message(what: str, searched: list[str], skipped: list[str]) -> str:
    name = clean_title(what) or "that"
    msg = (f"I couldn't find {name} in {searched_phrase(searched)}." if searched
           else f"I couldn't search for {name}: no media source answered.")
    if skipped:
        msg += f" I couldn't search {searched_phrase(skipped)}."
    return msg


async def play_pick(req: MediaPlayRequest, entity_id: str, ctx) -> ExecutionResult:
    """Play the option a person chose after a question, after checking its source is still usable."""
    try:
        cand = Candidate(**{k: v for k, v in (req.pick or {}).items() if k in Candidate.__dataclass_fields__})
    except TypeError:
        return ExecutionResult.fail("That choice could not be read.", service=SERVICE)
    screen, player = await target_facts(ctx, entity_id)
    sources = await discover_sources(ctx, screen=screen, wants_video=cand.group == "video")
    if not any(s.key == cand.source and s.ready for s in sources):
        return ExecutionResult.fail(f"{cand.title} came from a source that isn't available now.", service=SERVICE)
    return await play_candidate(cand, req, entity_id, ctx, player)


def failed_message(cand: Candidate, why: str | None, player: str) -> str:
    """What to say when the player would not play what was found: the item, and the reason in plain words."""
    name = cand.spoken()
    text = str(why or "")
    if re.search(r"no playable items", text, re.IGNORECASE):
        return f"I found {name}, but there's nothing in it I can play."
    if re.search(r"not connected to music assistant", text, re.IGNORECASE):
        return text
    reason = re.sub(r"^(?:Failed to play[^:]*:\s*)?(?:HA returned \d+:\s*)?(?:\d+:\s*)?", "", text).strip()
    return f"I found {name}, but {player} wouldn't play it" + (f": {reason}" if reason else ".")


async def play_candidate(cand: Candidate, req: MediaPlayRequest, entity_id: str, ctx,
                         player: str | None = None) -> ExecutionResult:
    """Hand the chosen item to the handler that already plays that kind on that player."""
    from services.execution.handlers import audiobookshelf as abs_handler
    from services.execution.handlers import media as media_handler

    player = player or entity_id
    log.info(f"[media/find] playing {cand.source}:{cand.kind}:{cand.title!r} ({cand.ident}) on {entity_id}")
    plain = req.model_copy(update={"search_everywhere": False, "pick": None, "fallback_query": None})
    if cand.source == "audiobookshelf":
        if cand.kind in ("series", "author"):
            abs_req = AudiobookshelfRequest(action="play_by", user_context=ctx, entity_id=entity_id,
                                            **({"series": cand.ident} if cand.kind == "series" else {"author": cand.ident}))
        elif cand.kind == "podcast":
            abs_req = AudiobookshelfRequest(action="play_podcast_episode", book_id=cand.ident, query=None,
                                            user_context=ctx, entity_id=entity_id)
        else:
            abs_req = AudiobookshelfRequest(action="play", book_id=cand.ident, user_context=ctx, entity_id=entity_id)
        result = await abs_handler.handle_audiobookshelf(abs_req)
    elif cand.source == "web_video":
        result = await media_handler.play_video(plain.model_copy(update={"query": cand.ident}), entity_id, ctx)
    elif cand.source == "music_assistant":
        result = await media_handler.play_music(
            plain.model_copy(update={"query": cand.ident, "media_content_type": None}), entity_id, ctx)
    else:
        return ExecutionResult.fail(f"I can't play {cand.title} from {cand.source}.", service=SERVICE)
    detail = {**(result.detail or {}), "found": cand.as_pick()}
    if result.status != "SUCCESS":
        if (result.detail or {}).get("playback") == "not_started":
            return result.model_copy(update={"detail": detail})  # already says it plainly
        return result.model_copy(update={"detail": detail, "message": failed_message(cand, result.message, player)})
    if cand.source != "web_video":
        from services.execution import stt_vocabulary

        stt_vocabulary.note_used(cand.title, *re.split(r",\s*|\s+&\s+", cand.by or ""))
    if cand.source == "web_video":
        said = f"the video {cand.title}" if cand.extra.get("title_known") else f"a video of {cand.title}"
    else:
        said = cand.spoken() + ","
    return ExecutionResult.ok(f"Playing {said} on {player}.", service=SERVICE, detail=detail)
