"""Online translation providers: fetch copyrighted text without bundling it.

A provider is a way to obtain a translation this install does not ship. The
family owns the licence; we never republish the text. Everything a provider
returns is written straight into the corpus by :func:`services.bible.corpus.import_corpus`
and then behaves exactly like a translation installed from a file -- the reader
cannot tell the difference, and nothing about the read path touches the network.

A provider is *configuration*, not code. ``corpus_manifest.json`` lists each one
under ``providers`` with its base URL and the global setting it needs, so an
install can add or retire a service without a deploy. A provider whose setting
is unset is skipped with a reason naming that setting -- never a guessed host and
never a silent fallback to a different translation.

Only one provider ships today: ``api.bible``, whose **API host is
``api.scripture.api.bible``** -- ``api.bible`` itself is the marketing site and
404s every ``/v1/*`` path. Which translations a key unlocks depends on the
licence, so the catalogue is fetched rather than assumed: a free key exposes
around 40 English translations including NIV and NLT, while ESV and NKJV are not
in the catalogue at all and still need publisher files.

Every chapter is kept on disk by :mod:`services.bible.provider_cache`, because a
whole Bible is roughly 1,190 requests against a metered allowance. A chapter
fetched once is never fetched again, and the cache stores the provider's *raw*
payload so a parser fix can be re-applied for free.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import aiohttp

from services.bible import books as book_table
from services.bible.corpus import MANIFEST_PATH, CorpusError
from services.bible.provider_cache import ChapterCache

PROVIDER_TIMEOUT = aiohttp.ClientTimeout(total=60.0)
CHAPTER_TIMEOUT = aiohttp.ClientTimeout(total=30.0)
USER_AGENT = "sharedllm-bible/1.0 (+family use)"
CHAPTER_DELAY_SECONDS = 0.0
_VERSION_MARKER = re.compile(r"\[(\d{1,3})\]")
_WHITESPACE = re.compile(r"\s+")


class ProviderError(ValueError):
    """The caller asked for something a provider cannot serve (a 400)."""


class ProviderUnavailable(RuntimeError):
    """A provider could not be reached or refused its configuration (a 503)."""


@dataclass(frozen=True)
class RemoteTranslation:
    """One translation a provider can hand over."""

    id: str
    name: str
    language: str = ""
    license_class: str = "licensed"
    rights_holder: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "language": self.language,
            "license_class": self.license_class,
            "rights_holder": self.rights_holder,
            "note": self.note,
        }


@dataclass
class Provider:
    """A named online service that supplies translations, declared in the manifest."""

    code: str
    title: str
    base_url: str
    requires: str = ""
    note: str = ""
    api_key: str = ""
    timeout: aiohttp.ClientTimeout = PROVIDER_TIMEOUT

    def configured(self) -> bool:
        """True when the setting this provider needs actually holds a value."""
        if not self.base_url:
            return False
        return bool(self.api_key) if self.requires else True

    def unconfigured_reason(self) -> str:
        """Why this provider cannot be used, naming the setting to fix."""
        if not self.base_url:
            return (
                f"{self.title} has no base_url in the corpus manifest, so there is no "
                "service to call. Add one rather than guessing a host."
            )
        if self.requires and not self.api_key:
            return (
                f"{self.title} needs the {self.requires} setting. Set it in Identity "
                f"under global settings (or the {self.requires} environment variable), then "
                "reload this page."
            )
        return ""

    def describe(self) -> dict:
        return {
            "code": self.code,
            "title": self.title,
            "base_url": self.base_url,
            "requires": self.requires,
            "note": self.note,
            "configured": self.configured(),
            "reason": self.unconfigured_reason(),
        }

    async def translations(self) -> list[RemoteTranslation]:
        raise NotImplementedError

    async def fetch(
        self,
        translation_id: str,
        destination: Path,
        *,
        progress: Callable[[int, int, str], None] | None = None,
        cache: "ChapterCache | None" = None,
        budget: int | None = None,
    ) -> tuple[Path, str]:
        """Download a whole Bible as corpus-shaped JSON; returns (path, name).

        The output is the same list-of-books shape :func:`corpus.import_corpus`
        expects, so the existing exact-chapter-count validation applies unchanged:
        a provider that silently drops a book produces a file that is refused,
        not a short Bible nobody notices.
        """
        raise NotImplementedError

    async def sample(self, translation_id: str, *, cache: "ChapterCache | None" = None) -> dict:
        """Fetch and parse one chapter so a dry run can prove the parsing works.

        A whole Bible is about 1,190 requests on api.bible's free plan, so the
        question "does this translation read back correctly?" has to be
        answerable for the price of one. The chapter is cached, so the real
        import does not pay for it twice.
        """
        raise ProviderError(
            f"{self.code} cannot produce a one-chapter sample, so it cannot be dry run."
        )


@dataclass
class ApiBibleProvider(Provider):
    """api.bible: a keyed catalogue of translations, fetched chapter by chapter.

    A whole Bible is roughly 1,180 chapter requests, so this takes minutes and
    says so through ``progress`` rather than appearing to hang.
    """

    async def _get(self, session: aiohttp.ClientSession, path: str, *, timeout: aiohttp.ClientTimeout | None = None) -> Any:
        url = f"{self.base_url.rstrip('/')}{path}"
        try:
            async with session.get(
                url, headers={"api-key": self.api_key, "accept": "application/json"}, timeout=timeout or self.timeout
            ) as resp:
                body = await resp.text()
                if resp.status >= 400:
                    detail = body.strip()[:400] or resp.reason or "no detail"
                    raise ProviderUnavailable(f"api.bible answered {resp.status} for {path}: {detail}")
        except aiohttp.ClientError as exc:
            raise ProviderUnavailable(f"api.bible could not be reached at {url}: {exc!s}") from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProviderUnavailable(
                f"api.bible returned something that is not JSON for {path} "
                f"({len(body)} bytes). Check that the base_url {self.base_url!r} is the API host."
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderUnavailable(f"api.bible returned a {type(payload).__name__} for {path}, expected an object")
        return payload.get("data")

    async def translations(self) -> list[RemoteTranslation]:
        if not self.configured():
            raise ProviderUnavailable(self.unconfigured_reason())
        async with aiohttp.ClientSession() as session:
            data = await self._get(session, "/v1/bibles")
        if not isinstance(data, list):
            raise ProviderUnavailable("api.bible returned no bible list; expected a data array")
        found: list[RemoteTranslation] = []
        for raw in data:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            found.append(
                RemoteTranslation(
                    id=str(raw.get("id")),
                    name=str(raw.get("name") or raw.get("englishName") or raw.get("id")),
                    language=str(raw.get("language") or ""),
                    license_class="licensed",
                    rights_holder=str(raw.get("copyright") or raw.get("publisher") or ""),
                    note=str(raw.get("description") or "")[:200],
                )
            )
        return sorted(found, key=lambda t: t.name.lower())

    async def fetch(
        self,
        translation_id: str,
        destination: Path,
        *,
        progress: Callable[[int, int, str], None] | None = None,
        cache: "ChapterCache | None" = None,
        budget: int | None = None,
    ) -> tuple[Path, str]:
        """Download a whole Bible as corpus-shaped JSON; returns (path, name).

        The output is the same list-of-books shape :func:`corpus.import_corpus`
        expects, so the existing exact-chapter-count validation applies unchanged:
        a provider that silently drops a book produces a file that is refused,
        not a short Bible nobody notices.

        ``cache`` turns a repeated import into a one-off cost. A chapter already
        on disk is served from it and never requested again -- a whole Bible is
        roughly 1,190 calls, so this is the difference between paying for a
        translation once and paying for it every time.

        ``budget`` is a ceiling on the calls *this* run may make, checked before
        the first request against the chapters that are not cached. It is a
        refusal, not a truncation: a run that would exceed it stops without
        writing anything.
        """
        if not self.configured():
            raise ProviderUnavailable(self.unconfigured_reason())
        translation_id = str(translation_id or "").strip()
        if not translation_id:
            raise ProviderError("Choose a translation to import; no provider id was given")

        async with aiohttp.ClientSession() as session:
            remote_name = await _remote_name(self, session, translation_id, cache=cache)
            catalogue = None if cache is not None else None
            if cache is not None:
                cached_books = cache.get_books()
                if isinstance(cached_books, list):
                    catalogue = cached_books
            if catalogue is None:
                catalogue = await self._call(session, cache, f"/v1/bibles/{translation_id}/books")
                if cache is not None:
                    cache.put_books(catalogue)
            if not isinstance(catalogue, list) or not catalogue:
                raise ProviderUnavailable(
                    f"api.bible listed no books for translation {translation_id!r}. "
                    "That id may not belong to this key."
                )
            by_osis: dict[str, str] = {}
            for raw in catalogue:
                if not isinstance(raw, dict):
                    continue
                book_id = str(raw.get("id") or raw.get("bookId") or "")
                osis = _osis(
                    book_id,
                    str(raw.get("nameLong") or ""),
                    str(raw.get("name") or ""),
                    str(raw.get("abbreviation") or ""),
                )
                if not osis or not book_id:
                    continue
                by_osis[osis] = book_id

            books_payload: list[dict] = []
            ordered = sorted(by_osis, key=lambda o: book_table.BOOK_BY_OSIS[o]["order"])
            total = len(ordered)

            wanted = [
                f"{by_osis[osis]}.{number}"
                for osis in ordered
                for number in range(1, book_table.BOOK_BY_OSIS[osis]["chapters"] + 1)
            ]
            outstanding = cache.missing_chapters(wanted) if cache is not None else wanted
            if budget is not None and len(outstanding) > budget:
                raise ProviderError(
                    f"{translation_id!r} needs {len(outstanding)} chapters that are not cached, but the "
                    f"budget for this import is {budget}. Nothing was fetched or written. Either raise "
                    "BIBLE_PROVIDER_CALL_BUDGET, or import in smaller pieces once the cache is warm."
                )

            for index, osis in enumerate(ordered, start=1):
                name = book_table.BOOK_BY_OSIS[osis]["name"]
                chapters: list[list[str]] = []
                for number in range(1, book_table.BOOK_BY_OSIS[osis]["chapters"] + 1):
                    chapter_id = f"{by_osis[osis]}.{number}"
                    chapter_payload = cache.get_chapter(chapter_id) if cache is not None else None
                    if chapter_payload is None:
                        chapter_payload = await self._call(
                            session,
                            cache,
                            f"/v1/bibles/{translation_id}/chapters/{chapter_id}?content-type=json",
                            timeout=CHAPTER_TIMEOUT,
                        )
                        if not chapter_payload:
                            raise ProviderUnavailable(
                                f"api.bible returned nothing for {name} {number}, so the file would "
                                "have a hole in it and was not written."
                            )
                        if cache is not None:
                            cache.put_chapter(chapter_id, chapter_payload)
                    chapters.append(_verses_of(chapter_payload, f"{name} {number}"))
                    if CHAPTER_DELAY_SECONDS:
                        await asyncio.sleep(CHAPTER_DELAY_SECONDS)
                books_payload.append({"abbrev": osis, "name": name, "chapters": chapters})
                if progress is not None:
                    progress(index, total, name)

        missing = [o for o in book_table.BOOK_ORDER if o not in {row["abbrev"] for row in books_payload}]
        if missing:
            first = book_table.BOOK_BY_OSIS[missing[0]]["name"]
            raise ProviderUnavailable(
                f"api.bible offered {total} of the {len(book_table.BOOK_ORDER)} books for {translation_id!r}; "
                f"{first} is missing, so the file would not be a complete Bible and was not written."
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(books_payload, ensure_ascii=False), encoding="utf-8")
        return destination, remote_name

    async def _call(
        self,
        session: aiohttp.ClientSession,
        cache: "ChapterCache | None",
        path: str,
        *,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> Any:
        """One provider request, counted so the log can show what it cost."""
        if cache is not None:
            cache.stats.calls += 1
        return await self._get(session, path, timeout=timeout)

    async def estimate(self, translation_id: str, *, cache: "ChapterCache | None" = None) -> dict:
        """What importing this translation would cost, before spending anything.

        Answers the question the Install button should ask: how many chapters are
        already cached, how many would still be requested, and therefore how many
        of the provider's monthly calls this would use. The book list comes from
        the cache when it is there, so an estimate on a warm translation costs
        no calls at all.
        """
        translation_id = str(translation_id or "").strip()
        if not translation_id:
            raise ProviderError("Choose a translation to estimate; no provider id was given")
        if not self.configured():
            raise ProviderUnavailable(self.unconfigured_reason())

        remote_name = translation_id
        catalogue = cache.get_books() if cache is not None else None
        calls = 0
        async with aiohttp.ClientSession() as session:
            if catalogue is None:
                remote_name = await _remote_name(self, session, translation_id, cache=cache)
                catalogue = await self._call(session, cache, f"/v1/bibles/{translation_id}/books")
                calls = (cache.stats.calls if cache is not None else 2) or 2
                if cache is not None:
                    cache.put_books(catalogue)
            else:
                calls = 0
        if not isinstance(catalogue, list) or not catalogue:
            raise ProviderUnavailable(
                f"api.bible listed no books for translation {translation_id!r}, so its cost cannot be estimated."
            )
        by_osis: dict[str, str] = {}
        for raw in catalogue:
            if not isinstance(raw, dict):
                continue
            book_id = str(raw.get("id") or raw.get("bookId") or "")
            osis = _osis(
                book_id,
                str(raw.get("nameLong") or ""),
                str(raw.get("name") or ""),
                str(raw.get("abbreviation") or ""),
            )
            if osis and book_id:
                by_osis[osis] = book_id
        wanted = [
            f"{by_osis[osis]}.{number}"
            for osis in sorted(by_osis, key=lambda o: book_table.BOOK_BY_OSIS[o]["order"])
            for number in range(1, book_table.BOOK_BY_OSIS[osis]["chapters"] + 1)
        ]
        cached = cache.cached_chapters(wanted) if cache is not None else 0
        return {
            "translation_id": translation_id,
            "name": remote_name,
            "books": len(by_osis),
            "chapters": len(wanted),
            "cached": cached,
            "remaining": len(wanted) - cached,
            "calls": len(wanted) - cached + (calls if cache is None else 0),
            "cache_directory": cache.stats.directory if cache is not None else "",
        }

    async def sample(self, translation_id: str, *, cache: "ChapterCache | None" = None) -> dict:
        """Read back one chapter of Genesis, the way a reader would see it.

        Genesis is deliberate rather than arbitrary: it is first in our canonical
        order, so it exercises the same book mapping an import would, and its
        opening chapter is dense with footnotes and section headings, which is
        exactly where a parser goes wrong.
        """
        translation_id = str(translation_id or "").strip()
        if not translation_id:
            raise ProviderError("Choose a translation to sample; no provider id was given")
        if not self.configured():
            raise ProviderUnavailable(self.unconfigured_reason())

        async with aiohttp.ClientSession() as session:
            name = await _remote_name(self, session, translation_id, cache=cache)
            catalogue = cache.get_books() if cache is not None else None
            if catalogue is None:
                catalogue = await self._call(session, cache, f"/v1/bibles/{translation_id}/books")
                if cache is not None:
                    cache.put_books(catalogue)
            book_id = ""
            if isinstance(catalogue, list):
                for raw in catalogue:
                    if not isinstance(raw, dict):
                        continue
                    if _osis(
                        str(raw.get("id") or raw.get("bookId") or ""),
                        str(raw.get("nameLong") or ""),
                        str(raw.get("name") or ""),
                        str(raw.get("abbreviation") or ""),
                    ) == "Gen":
                        book_id = str(raw.get("id") or raw.get("bookId") or "")
                        break
            if not book_id:
                raise ProviderUnavailable(
                    f"{name} does not list a book we recognise as Genesis, so it cannot be sampled."
                )
            chapter_id = f"{book_id}.1"
            payload = cache.get_chapter(chapter_id) if cache is not None else None
            if payload is None:
                payload = await self._call(
                    session, cache, f"/v1/bibles/{translation_id}/chapters/{chapter_id}"
                )
            if not payload:
                raise ProviderUnavailable(
                    f"api.bible returned nothing for Genesis 1 of {name}, so it could not be sampled."
                )
            if cache is not None:
                cache.put_chapter(chapter_id, payload)

        verses = _verses_of(payload, "Genesis 1")
        if not verses:
            raise ProviderUnavailable(
                f"Genesis 1 of {name} came back with no verses at all, so it was not parsed."
            )
        first = verses[0]
        return {
            "translation_id": translation_id,
            "name": name,
            "chapter_id": chapter_id,
            "reference": "Genesis 1:1",
            "text": first,
            "verse_count": len(verses),
            "requests_used": cache.stats.calls if cache is not None else 2,
        }




def _osis(token: str, *alternates: str) -> str:
    """Map a provider's book token onto our canonical OSIS id.

    api.bible writes books as ``GEN`` or ``43JHN`` -- the short OSIS form with an
    optional two-digit ordinal in front -- but its abbreviations are its own
    (``JHN``, ``JUD``, ``PRO``), so the id alone does not always resolve. The
    book's own names are offered as alternates for exactly that reason.

    The longest candidate that resolves wins rather than the first. That
    distinction is load-bearing: ``JUD`` is a listed abbreviation of *Jude*, so
    trying the id before the name would file the book of Judges under Jude and
    quietly lose a book. Anything that still does not resolve is skipped rather
    than guessed at, so a rename upstream costs one book and a clear refusal
    from the completeness check, never a misfiled verse.
    """
    best = ""
    best_length = 0
    for candidate in (token, *alternates):
        text = str(candidate or "").strip()
        if not text or len(text) <= best_length:
            continue
        resolved = book_table.resolve_book(text)
        if not resolved and len(text) > 2 and text[:2].isdigit():
            resolved = book_table.resolve_book(text[2:])
        if resolved:
            best, best_length = resolved, len(text)
    return best


def _node_text(node: Any) -> str:
    """The readable text of a node, descending through whatever it wraps."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_node_text(item) for item in node)
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text") or "")
    return _node_text(node.get("items") or [])


def _verse_texts(tree: Any) -> dict[int, str]:
    """Walk a chapter's node tree into ``{verse number: text}``.

    api.bible marks verses with a ``verse`` tag whose only content is the verse
    number; the words follow in *sibling* nodes that carry ``attrs.verseId``.
    So a verse runs from its marker to the next one, exactly as it does in a
    study Bible's markup: everything after the marker belongs to it until the
    next marker. Section headings (``para`` with an ``s``-style) close the open
    verse without contributing to it, which is how an introduction that appears
    before verse 1 is kept out of the text.
    """
    found: dict[int, str] = {}
    parts: list[str] = []
    current: int | None = None

    def walk(node: Any) -> None:
        nonlocal current
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        name = str(node.get("name") or "")
        attrs = node.get("attrs") or {}
        if name == "verse":
            if current is not None:
                found[current] = _WHITESPACE.sub(" ", "".join(parts)).strip()
            current = None
            parts.clear()
            try:
                current = int(str(attrs.get("number")))
            except (TypeError, ValueError):
                current = None
            return
        if name == "para" and str(attrs.get("style") or "").startswith("s"):
            if current is not None:
                found[current] = _WHITESPACE.sub(" ", "".join(parts)).strip()
            current = None
            parts.clear()
            return
        children = node.get("items")
        if current is not None:
            if children:
                # Entering a container. Words either side of it are separate
                # nodes and nothing guarantees a space between them, so put one
                # in unless the previous piece already ended with whitespace.
                if parts and not parts[-1].endswith(" "):
                    parts.append(" ")
            else:
                # A leaf carries words, or it is a whitespace carrier with no
                # ``text`` of its own -- either way an empty leaf still has to
                # leave a space behind, or "evil,they" comes out of two good
                # words.
                parts.append(_node_text(node) or " ")
        walk(children or [])

    walk(tree)
    if current is not None:
        found[current] = _WHITESPACE.sub(" ", "".join(parts)).strip()
    return found


def _ordered(found: dict[int, str], label: str) -> list[str]:
    """Verse numbers must run 1, 2, 3... or nothing is returned.

    Both dialects can hand back a chapter whose numbering has a hole, and a hole
    means every verse after it would be filed under the wrong number. Refusing is
    the only safe response: the caller treats a refusal as "not written", so a
    malformed chapter costs a message rather than a silently scrambled Bible.
    """
    verses: list[str] = []
    for number in sorted(found):
        if number != len(verses) + 1:
            where = f" for {label}" if label else ""
            raise ProviderUnavailable(
                f"api.bible{where} jumped to verse {number} after {len(verses)}; the markers are not a "
                "clean 1, 2, 3 run, so every later verse number would be wrong. Nothing was written."
            )
        verses.append(found[number])
    return verses


def _verses_of(data: Any, label: str = "", declared: int | None = None) -> list[str]:
    """A chapter payload -> a list of verse texts, numbered from 1.

    The **node tree** (``content-type=json``) is what this fetches. It is the only
    dialect that marks a verse boundary reliably: a ``verse`` tag carries the
    number and the words follow until the next ``verse`` tag, so a section heading
    ends the previous verse instead of becoming one.

    The plain-text dialect is still accepted, but it is not trusted by
    :meth:`ApiBibleProvider.fetch`, and why is worth recording: api.bible tags a
    section heading with the *next* verse number, so ``Mark 11:26`` comes back as
    ``[26] The Authority of Jesus Questioned`` -- a heading wearing a verse
    number. Walking that as text invents a verse and folds 1,204 headings into the
    preceding verses across one Bible. The node tree shows the same positions as
    empty, which is what NIV2011 actually has.

    The payload's own ``verseCount`` is deliberately **not** a refusal trigger. On
    this host it is not the verse count -- Numbers 1 declares 42 and has 54
    verses -- so treating a disagreement as corruption would refuse a perfectly
    good chapter.
    """
    text = ""
    if isinstance(data, dict):
        content = data.get("content")
        if isinstance(content, str):
            text = content
        elif "content" in data:
            found = _verse_texts(content)
            return _ordered(found, label)
        else:
            return _legacy_verses(data.get("verses") or [])
    elif isinstance(data, str):
        text = data
    elif isinstance(data, list):
        found = _verse_texts(data)
        return _ordered(found, label)
    else:
        return []

    return _ordered(dict(_bracketed_verses(text)), label)


async def _remote_name(
    provider: "ApiBibleProvider",
    session: Any,
    translation_id: str,
    *,
    cache: "ChapterCache | None" = None,
) -> str:
    """The translation's own name, so a reader never sees ``78a9f6124f344018-01``.

    Asked once and then kept in the cache, because the name costs a request like
    any other and there is no reason to pay it again.

    Falls back to the id rather than raising: a missing name is cosmetic, and a
    refusal here would throw away a perfectly good download.
    """
    if cache is not None:
        known = cache.get_name()
        if known:
            return known
    try:
        data = await provider._call(session, cache, f"/v1/bibles/{translation_id}")
    except (ProviderError, ProviderUnavailable, OSError):
        return translation_id
    entry = data.get("data") if isinstance(data, dict) else None
    if isinstance(entry, dict):
        name = str(entry.get("name") or "").strip()
        if name:
            if cache is not None:
                cache.put_name(name)
            return name
    return translation_id


def _bracketed_verses(text: str) -> list[tuple[int, str]]:
    """Split ``"heading [1] one [2] two"`` into ``[(1, "one"), (2, "two")]``.

    Each ``[n]`` owns the text that follows it up to the next marker, and the
    final marker owns the tail. Whatever precedes the first marker is the
    chapter heading, so it is dropped rather than becoming verse 1 -- Numbers 1
    opens with ``Registration of Israel's Troops`` before its ``[1]``, and reading
    that as a verse would shift every number in the chapter by one.
    """
    matches = list(_VERSION_MARKER.finditer(text))
    parts: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        parts.append((int(match.group(1)), _WHITESPACE.sub(" ", text[start:end]).strip()))
    return parts


def _legacy_verses(entries: Any) -> list[str]:
    """The flat ``{"verses": [{"text": ...}]}`` shape some deployments return."""
    if not isinstance(entries, list):
        return []
    texts = []
    for entry in entries:
        if isinstance(entry, dict):
            texts.append(str(entry.get("text") or ""))
        else:
            texts.append(str(entry or ""))
    return texts


@dataclass
class ProviderRegistry:
    """The providers this install offers, in the order they are attempted."""

    entries: list[Provider] = field(default_factory=list)

    def codes(self) -> list[str]:
        return [p.code for p in self.entries]

    def describe(self) -> list[dict]:
        return [p.describe() for p in self.entries]

    def get(self, code: str) -> Provider:
        wanted = str(code or "").strip()
        for provider in self.entries:
            if provider.code == wanted:
                return provider
        known = ", ".join(self.codes()) or "none"
        raise ProviderError(f"{code!r} is not a translation provider. This install offers: {known}")

    def configured(self) -> list[Provider]:
        return [p for p in self.entries if p.configured()]


def load_provider_config(path: Path | None = None) -> list[dict]:
    """Read the ``providers`` block of the corpus manifest.

    An install may ship no providers at all -- everything then comes from files,
    which is a supported configuration rather than a missing feature.
    """
    manifest_path = path or MANIFEST_PATH
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorpusError(f"corpus manifest not found: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise CorpusError(f"{manifest_path.name} is not valid JSON: {exc}") from exc
    raw = payload.get("providers")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CorpusError(f"{manifest_path.name} has a providers block that is not a list")
    entries: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise CorpusError(f"{manifest_path.name} has a non-object provider entry")
        code = str(item.get("code") or "").strip()
        if not code:
            raise CorpusError(f"{manifest_path.name} has a provider with no code")
        if code in seen:
            raise CorpusError(f"{manifest_path.name} lists provider {code!r} twice")
        seen.add(code)
        entries.append(
            {
                "code": code,
                "title": str(item.get("title") or code),
                "base_url": str(item.get("base_url") or "").strip(),
                "requires": str(item.get("requires") or "").strip(),
                "note": str(item.get("note") or "").strip(),
            }
        )
    return entries


def build_registry(entries: list[dict], *, settings: dict | None = None) -> ProviderRegistry:
    """Instantiate the declared providers, reading their keys from settings.

    ``settings`` is the resolved runtime configuration. A provider whose key is
    absent is still returned -- unconfigured, with a reason -- so the admin page
    can say what to set instead of pretending the provider does not exist.
    """
    resolved = settings or {}
    providers: list[Provider] = []
    for entry in entries:
        requires = entry.get("requires") or ""
        api_key = str(resolved.get(requires, "") or "") if requires else ""
        if entry["code"] == "api.bible":
            providers.append(ApiBibleProvider(api_key=api_key, **entry))
        else:
            raise ProviderError(
                f"provider {entry['code']!r} is declared in the corpus manifest but has no "
                "implementation in services/bible/providers.py"
            )
    return ProviderRegistry(entries=providers)