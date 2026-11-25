"""The household's words for speech to text: names the family says that Whisper has never seen.

Whisper hears names it does not know as ordinary words: an artist as "plate random late",
a playlist as "so fast", a book as "Wilder Hart". Its ``initial_prompt`` biases it towards
words it is shown, and the Wyoming Whisper servers the voice satellites use read that
prompt from Home Assistant's names API on every utterance (``--hass-api``/``--hass-token``,
refreshed while the person is still speaking, so it costs no latency). The gateway serves
that API from this vocabulary (``services/gateway/stt_vocabulary_routes.py``).

What is in it, all read from the integrations that are set up, never written here:

* rooms (the hub's areas) and the players the household can name ("the Office Echo");
* the family's first names (Identity's name roster);
* Music Assistant's artists, playlists and radio stations, most played first;
* Audiobookshelf's titles, series and authors, most recently added first;
* the book library's (Calibre) titles, the most recently added ``MAX_BOOKS``;
* ahead of all of these, what was played most recently (``note_used``, the titles and
  artists the media finder played), so what the family is listening to this week is always
  in the prompt, whatever its place in a library of thousands.

Whisper's prompt is cut at about 223 tokens, and a long prompt makes it echo words nobody
said, so the list is short: ``MAX_NAMES`` names and ``MAX_CHARS`` characters, taken from
each kind in turn so one big library cannot crowd out the rest. The Whisper server counts
the tokens itself and stops at the first name that does not fit, so the order here is the
order of importance. It is rebuilt every ``REFRESH_S``; a source that fails keeps its last
names and is reported, and an empty vocabulary only means no biasing.

The same names feed ``media_vocabulary``, the misheard-name correction the media handlers
use when a search finds nothing.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from services.execution import media_vocabulary

log = logging.getLogger("execution.stt_vocabulary")
log.setLevel(logging.INFO)

#: How often the vocabulary is rebuilt from the integrations.
REFRESH_S = 30 * 60
#: How long the first build waits after startup (the other services come up too).
FIRST_BUILD_DELAY_S = 20
#: The most names offered to speech to text ...
MAX_NAMES = 90
#: ... and the most characters (Whisper's tokenizer is about three characters a token for names;
#: its own budget, 200 tokens by default, decides exactly how many of these it keeps).
MAX_CHARS = 700
#: The most names read from each kind before the turns are taken.
MAX_PER_KIND = 200
#: The book library can hold thousands; only the newest this many titles are candidates.
MAX_BOOKS = 40
#: A name longer than this is a sentence or a subtitle, not something said to a speaker.
MAX_NAME_CHARS = 40
#: Every source answers within this or is skipped this round.
SOURCE_TIMEOUT_S = 30.0
#: The order kinds are taken in: what is said most and misheard most first.
NAME_KINDS = ("person", "artist", "playlist", "title", "series", "author", "radio", "book")

_HOUSEHOLD = "household"
#: Accounts that are not people: never offered as a name.
_NOT_PEOPLE = frozenset({_HOUSEHOLD, "default", "admin", "system", "guest", "jarvis", "raven"})
#: Words a player's name ends with that say what kind of device it is, not which one
#: ("Office Echo Media Player" is said "the Office Echo").
_DEVICE_SUFFIX = re.compile(
    r"(?:\s+(?:media\s+player|assist(?:ant)?\s+satellite|smart[\s-]speaker|speaker|remote|cast))+$",
    re.IGNORECASE)


@dataclass
class Vocabulary:
    """One build: the rooms, the names in order, the players, and which sources answered."""

    rooms: list[str] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)
    built_at: float = 0.0
    sources: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"rooms": self.rooms, "names": self.names, "devices": self.devices,
                "built_at": self.built_at, "sources": self.sources}

    def prompt(self) -> str:
        """The vocabulary as one Whisper prompt line, in the order it is offered ("" when empty)."""
        words = _dedupe([*self.names, *self.rooms, *self.devices])
        return (", ".join(words) + ".") if words else ""


#: How many recently played names are kept ahead of the rest.
MAX_RECENT = 20

_current = Vocabulary()
_last_good: dict[str, list[str]] = {}
_recent: list[str] = []


def current() -> Vocabulary:
    return _current


def note_used(*names: object) -> None:
    """Put the names of something just played (its title, its artist) at the front of the vocabulary."""
    for raw in reversed(names):  # the title stays ahead of its artist
        name = clean_name(raw)
        if not name:
            continue
        key = media_vocabulary.squash(name)
        _recent[:] = [n for n in _recent if media_vocabulary.squash(n) != key]
        _recent.insert(0, name)
    del _recent[MAX_RECENT:]


def served() -> dict[str, Any]:
    """The vocabulary as it is offered now: the latest build, with what was played recently first."""
    vocab = _current.as_dict()
    if _recent:
        # Recent names first, then the build's order; the same bounds as a build.
        vocab["names"] = _bounded(_dedupe([*_recent, *_current.names]))
    return vocab


def _bounded(names: list[str], max_names: int = MAX_NAMES, max_chars: int = MAX_CHARS) -> list[str]:
    out: list[str] = []
    used = 0
    for name in names[:max_names]:
        if used + len(name) + 2 > max_chars:
            break
        out.append(name)
        used += len(name) + 2
    return out


def clean_name(raw: object) -> str:
    """A name as it is said: no "(Unabridged)", "[tags]", "| subtitle" or ": subtitle"; "" when unusable."""
    text = str(raw or "")
    text = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", text)
    text = re.split(r"\s+[|]\s+|:\s+|\s+-\s+", text, maxsplit=1)[0]
    text = re.sub(r"\s*#.*$", "", text)  # "Moon Garden #1": the series, not its number
    text = " ".join(text.split()).strip(" ,.;")
    if not text or len(text) > MAX_NAME_CHARS or not re.search(r"[A-Za-z]", text):
        return ""
    return text


def _dedupe(names: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        key = media_vocabulary.squash(n)
        if key and key not in seen:
            seen.add(key)
            out.append(n)
    return out


def interleave(kinds: dict[str, list[str]], order: Iterable[str] = NAME_KINDS,
               max_names: int = MAX_NAMES, max_chars: int = MAX_CHARS) -> list[str]:
    """One name from each kind in turn, until ``max_names`` or ``max_chars`` (", " joined)."""
    pools = [list(_dedupe(clean_name(n) for n in kinds.get(k, [])))[:MAX_PER_KIND] for k in order]
    out: list[str] = []
    seen: set[str] = set()
    used = 0
    while len(out) < max_names and any(pools):
        for pool in pools:
            if not pool or len(out) >= max_names:
                continue
            name = pool.pop(0)
            key = media_vocabulary.squash(name)
            if not key or key in seen:
                continue
            cost = len(name) + 2
            if used + cost > max_chars:
                return out
            seen.add(key)
            out.append(name)
            used += cost
    return out


def build(found: dict[str, list[str]], sources: dict[str, str] | None = None) -> Vocabulary:
    """The vocabulary from what each kind of source found (rooms and devices kept apart)."""
    rooms = _dedupe(clean_name(r) for r in found.get("room", []))
    devices = _dedupe(clean_name(d) for d in found.get("device", []))
    room_keys = {media_vocabulary.squash(r) for r in rooms}
    devices = [d for d in devices if media_vocabulary.squash(d) not in room_keys]
    names = interleave(found)
    return Vocabulary(rooms=rooms[:30], names=names, devices=devices[:30], built_at=time.time(),
                      sources=dict(sources or {}))


# --------------------------------------------------------------------------- #
# Reading the integrations                                                     #
# --------------------------------------------------------------------------- #

async def _household_ctx():
    """The household's credentials as a UserContext, with the hub's login; None when Identity cannot say."""
    import aiohttp

    from services.common.hub_login import hub_pair
    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET
    from services.execution.schemas import UserContext

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as client, client.post(
            f"{IDENTITY_SVC_URL}/api/resolve", json={"household": True},
            headers={"X-Internal-Secret": INTERNAL_SECRET},
        ) as resp:
            creds = await resp.json() if resp.status == 200 else None
    except Exception as exc:
        log.warning("[vocabulary] could not ask Identity for the household's credentials: %s", exc)
        return None
    if not isinstance(creds, dict):
        return None
    ha_url, ha_token = hub_pair(creds)
    data = {**creds, "user": creds.get("user") or creds.get("username") or _HOUSEHOLD,
            "ha_url": ha_url or None, "ha_token": ha_token or None}
    return UserContext.model_validate(data)


async def music_names(ctx) -> dict[str, list[str]]:
    """Music Assistant's artists, playlists and stations, most played first."""
    from services.execution import media_finder

    source = await media_finder.music_assistant_source(ctx)
    if source is None or not source.ready:
        raise RuntimeError(source.why if source else "not set up")
    out: dict[str, list[str]] = {}
    for kind, vocab_kind in (("artist", "artist"), ("playlist", "playlist"), ("radio", "radio")):
        out[vocab_kind] = await _ma_library(ctx, source, kind)
    return out


async def _ma_library(ctx, source, kind: str) -> list[str]:
    if source.route == "hub":
        from services.execution.handlers import mass_ha_client

        items: list[dict] = []
        for order in ("play_count_desc", ""):
            try:
                items = await mass_ha_client.get_library(ctx.ha_url, ctx.ha_token, kind, limit=MAX_PER_KIND,
                                                         order_by=order, mass_entry_id=source.entry_id)
            except Exception as exc:
                log.info("[vocabulary] Music Assistant %s list (order %r) failed: %s", kind, order or "none", exc)
                items = []
            if items:
                break
        return [str(i.get("name") or "") for i in items or []]
    from services.execution.handlers import mass_client

    command = {"artist": "music/artists/library_items", "playlist": "music/playlists/library_items",
               "radio": "music/radios/library_items"}[kind]
    raw = await mass_client._ma_api(ctx.mass_url, ctx.mass_token, command,
                                    {"limit": MAX_PER_KIND, "order_by": "play_count_desc"})
    if not raw:
        raw = await mass_client._ma_api(ctx.mass_url, ctx.mass_token, command, {"limit": MAX_PER_KIND})
    return [str(i.get("name") or "") for i in raw or [] if isinstance(i, dict)]


async def audiobook_names(ctx) -> dict[str, list[str]]:
    """Audiobookshelf's titles, series and authors, most recently added first."""
    from services.execution import media_finder

    if media_finder.audiobookshelf_source(ctx) is None:
        raise RuntimeError("not set up")
    items = sorted(await media_finder.abs_catalogue(ctx), key=lambda i: -float(i.get("added") or 0))
    out: dict[str, list[str]] = {"title": [], "series": [], "author": []}
    for item in items:
        out["title"].append(item.get("title") or "")
        out["series"].extend(item.get("series") or [])
        out["author"].extend(a for a in re.split(r",\s*|\s+&\s+", item.get("author") or "") if a)
    return out


async def book_names(ctx) -> dict[str, list[str]]:
    """The newest ``MAX_BOOKS`` titles in the book library (Calibre ids grow as books are added)."""
    from services.execution import media_finder
    from services.execution.handlers import calibre as calibre_handler

    source = await media_finder.calibre_source(ctx)
    if source is None or not source.ready:
        raise RuntimeError(source.why if source else "not set up")
    books, _total = await calibre_handler._client().list_books(limit=100000)

    def newest(b: dict) -> int:
        try:
            return int(b.get("book_id") or 0)
        except (TypeError, ValueError):
            return 0

    return {"book": [str(b.get("title") or "") for b in sorted(books, key=newest, reverse=True)[:MAX_BOOKS]]}


async def people_names(_ctx) -> dict[str, list[str]]:
    """Each family member's first name (Identity's name roster), never the shared household account."""
    import aiohttp

    from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as client, client.get(
        f"{IDENTITY_SVC_URL}/api/internal/name-roster", headers={"X-Internal-Secret": INTERNAL_SECRET},
    ) as resp:
        resp.raise_for_status()
        roster = await resp.json()
    names = []
    for person in roster or []:
        if not isinstance(person, dict) or str(person.get("username") or "").lower() in _NOT_PEOPLE:
            continue
        display = str(person.get("display_name") or person.get("username") or "").strip()
        if display.lower() in _NOT_PEOPLE:
            continue
        first = display.split()[0] if display else ""
        if len(first) >= 2:
            names.append(first)
    return {"person": names}


async def place_names(ctx) -> dict[str, list[str]]:
    """The hub's rooms and the friendly names of its media players."""
    from services.execution import capability_index, ha_client

    if not (ctx.ha_url and ctx.ha_token):
        raise RuntimeError("no home hub")
    idx = await capability_index.load(ctx.ha_url, ctx.ha_token)
    if idx is None:
        raise RuntimeError("the hub has no capability index")
    states = {s.get("entity_id"): s for s in await ha_client.get_states(ctx.ha_url, ctx.ha_token) or []
              if isinstance(s, dict)}
    devices = []
    for eid in idx.media_targets():
        name = _DEVICE_SUFFIX.sub("", str(((states.get(eid) or {}).get("attributes") or {}).get("friendly_name") or ""))
        if name.strip():
            devices.append(name.strip())
    return {"room": idx.area_names(), "device": devices}


SOURCES: dict[str, Callable[[Any], Awaitable[dict[str, list[str]]]]] = {
    "places": place_names,
    "people": people_names,
    "music": music_names,
    "audiobooks": audiobook_names,
    "books": book_names,
}

#: Which media_vocabulary kind (the misheard-name correction) each vocabulary kind feeds.
_CORRECTION_KINDS = {"artist": "artist", "playlist": "playlist", "author": "author", "series": "series",
                     "title": "title"}


async def collect(ctx, sources: dict[str, Callable[[Any], Awaitable[dict[str, list[str]]]]] | None = None
                  ) -> tuple[dict[str, list[str]], dict[str, str]]:
    """(names by kind, each source's outcome) from every source; a failed one keeps its last names."""
    sources = SOURCES if sources is None else sources
    keys = list(sources)
    results = await asyncio.gather(
        *(asyncio.wait_for(sources[k](ctx), SOURCE_TIMEOUT_S) for k in keys), return_exceptions=True)
    found: dict[str, list[str]] = {}
    outcome: dict[str, str] = {}
    for key, result in zip(keys, results, strict=True):
        if isinstance(result, BaseException):
            why = "did not answer in time" if isinstance(result, TimeoutError) else (str(result) or type(result).__name__)
            outcome[key] = f"skipped: {why}"
            kept = {k: v for k, v in _last_good.items() if k.startswith(f"{key}:")}
            for kk, names in kept.items():
                found.setdefault(kk.split(":", 1)[1], []).extend(names)
            continue
        counts = []
        for kind, names in (result or {}).items():
            names = [n for n in names if n]
            _last_good[f"{key}:{kind}"] = names
            found.setdefault(kind, []).extend(names)
            counts.append(f"{len(names)} {kind}")
        outcome[key] = ", ".join(counts) or "nothing"
    return found, outcome


async def refresh(sources: dict[str, Callable[[Any], Awaitable[dict[str, list[str]]]]] | None = None) -> Vocabulary:
    """Rebuild the vocabulary now; returns it (the previous one stays when Identity cannot be asked)."""
    global _current
    ctx = await _household_ctx()
    if ctx is None:
        log.warning("[vocabulary] Identity gave no household credentials; keeping the previous vocabulary")
        return _current
    found, outcome = await collect(ctx, sources)
    vocab = build(found, outcome)
    for kind, vocab_kind in _CORRECTION_KINDS.items():
        if found.get(kind):
            media_vocabulary.remember(vocab_kind, reversed(found[kind][:media_vocabulary.MAX_PER_KIND]))
    _current = vocab
    log.info("[vocabulary] %d room(s), %d player(s), %d name(s) for speech to text (%s)", len(vocab.rooms),
             len(vocab.devices), len(vocab.names), "; ".join(f"{k}: {v}" for k, v in outcome.items()))
    return vocab


async def refresh_loop() -> None:
    """Rebuild the vocabulary every ``REFRESH_S`` until cancelled."""
    await asyncio.sleep(FIRST_BUILD_DELAY_S)
    while True:
        try:
            await refresh()
        except Exception as exc:  # the vocabulary is a help, never a failure
            log.warning("[vocabulary] could not rebuild the speech-to-text vocabulary: %s", exc)
        await asyncio.sleep(REFRESH_S)


def reset() -> None:
    """Forget everything (tests)."""
    global _current
    _current = Vocabulary()
    _last_good.clear()
    _recent.clear()
