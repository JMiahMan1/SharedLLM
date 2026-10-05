"""A long-term on-disk cache of chapters fetched from a translation provider.

Scripture does not change, so a chapter that has been fetched once should never
be fetched again -- not on a retry, not next month, not when the same translation
is re-imported after a study Bible is added to it. This module is the reason a
second import of the same translation costs nothing.

The cache is deliberately outside the database. It holds provider payload that is
cheap to regenerate in principle but expensive in practice (a whole Bible is over
a thousand HTTP calls), and it needs to survive a wiped database.

What is cached
    The provider's book list, and one file per chapter, each holding the payload
    **exactly as it arrived**. Nothing else: a failed request is never written, so
    a refusal costs the same whether the cache is warm or cold.

Why the raw payload rather than parsed verses
    Scripture text does not change, but the way a provider frames it does. api.bible
    can tag a section heading with the next verse number, so the ``[n]`` markers in
    its plain-text form are not a reliable verse boundary while its node tree is.
    Keeping the bytes means a parser fix can be re-applied to a warm cache for free;
    storing parsed verses would bake in the bug and force a refetch.

How a cache entry is identified
    A directory per ``(provider, base URL, dialect, translation)``, hashed. Change
    the host, the ``content-type`` dialect or the translation id and the old tree
    is simply not consulted, rather than silently serving one server's text for
    another. The provider's api key is **not** part of the key, so rotating a key
    does not throw away the cache; a fingerprint of it is stored in the metadata
    only so an operator can tell which key filled it.

Durability
    Every write goes to a temporary file in the same directory and is then
    renamed, so a process killed mid-write cannot leave a half chapter that reads
    as complete.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CACHE_VERSION = "usfm-tree-v1"
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def cache_root() -> Path | None:
    """Where the cache lives, or ``None`` when it cannot be determined.

    ``BIBLE_PROVIDER_CACHE`` wins. Otherwise the directory is *derived* from the
    configured database path, because that path is already configuration and the
    cache belongs on the same volume as the text it feeds -- deriving it keeps the
    operator from having to set a second path that must agree with the first.
    Nothing is guessed about any remote value.
    """
    import services.config as cfg

    explicit = str(getattr(cfg, "BIBLE_PROVIDER_CACHE", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    database = str(getattr(cfg, "BIBLE_DATABASE_URL", "") or "").strip()
    if database.startswith("sqlite") and "///" in database:
        tail = database.split("///", 1)[1]
        if tail and tail != ":memory:":
            return Path(tail).expanduser().parent / "provider-cache"
    return None


def call_budget() -> int | None:
    """The most new HTTP calls one import may make, or ``None`` for no limit.

    ``BIBLE_PROVIDER_CALL_BUDGET`` is compared against the number of chapters that
    are *not* already cached, so a warm cache costs nothing against the budget
    however large the translation is.

    A value that is set but unreadable is an error rather than "no limit". This
    number is the only thing standing between a mistyped setting and a wasted
    provider allowance, so quietly treating ``12oo`` as unlimited would remove the
    guardrail exactly when it was needed.
    """
    import services.config as cfg

    raw = str(getattr(cfg, "BIBLE_PROVIDER_CALL_BUDGET", "") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"BIBLE_PROVIDER_CALL_BUDGET is {raw!r}, which is not a whole number of "
            "requests. Set it to a count (or leave it blank for no ceiling)."
        ) from exc
    if value <= 0:
        raise ValueError(
            f"BIBLE_PROVIDER_CALL_BUDGET is {value!r}; a request budget must be a "
            "positive count. Leave it blank if there should be no ceiling."
        )
    return value


@dataclass
class CacheStats:
    """What a fetch did, for the log the operator reads afterwards."""

    directory: str = ""
    books_hits: int = 0
    chapter_hits: int = 0
    chapter_writes: int = 0
    calls: int = 0

    @property
    def served(self) -> int:
        return self.books_hits + self.chapter_hits

    def as_dict(self) -> dict:
        return {
            "directory": self.directory,
            "served_from_cache": self.served,
            "chapters_cached": self.chapter_hits,
            "chapters_written": self.chapter_writes,
            "calls": self.calls,
        }


class ChapterCache:
    """One translation's cached book list and chapters."""

    def __init__(
        self,
        root: Path | None,
        *,
        provider_code: str,
        base_url: str,
        translation_id: str,
        key_fingerprint: str = "",
    ) -> None:
        self.provider_code = provider_code
        self.base_url = base_url
        self.translation_id = translation_id
        self.key_fingerprint = key_fingerprint
        self.stats = CacheStats()
        self.reason = ""
        self.root: Path | None = None
        self.translation_dir: Path | None = None
        if root is None:
            self.reason = (
                "no provider cache directory is configured, so every chapter will be "
                "fetched again; set BIBLE_PROVIDER_CACHE (or BIBLE_DATABASE_URL so one "
                "can be derived) to make this import a one-off cost"
            )
            return
        try:
            token = hashlib.sha256(
                "|".join(
                    [provider_code, base_url.rstrip("/"), CACHE_VERSION, translation_id]
                ).encode("utf-8")
            ).hexdigest()[:16]
            safe = _SAFE.sub("-", translation_id).strip("-") or "translation"
            self.translation_dir = Path(root) / _SAFE.sub("-", provider_code) / f"{safe}-{token}"
            self.translation_dir.mkdir(parents=True, exist_ok=True)
            self.root = Path(root)
            self.stats.directory = str(self.translation_dir)
            self._write_meta()
        except OSError as exc:
            self.translation_dir = None
            self.root = None
            self.reason = f"the provider cache directory could not be used ({exc})"

    @property
    def enabled(self) -> bool:
        return self.translation_dir is not None

    def _write_meta(self) -> None:
        path = self._path("meta.json")
        if path is None or path.exists():
            return
        _atomic_write(
            path,
            json.dumps(
                {
                    "kind": "jarvis.bible.provider-cache",
                    "version": CACHE_VERSION,
                    "provider": self.provider_code,
                    "base_url": self.base_url,
                    "translation_id": self.translation_id,
                    "key_fingerprint": self.key_fingerprint,
                    "created_at": _now(),
                    "note": (
                        "Scripture text does not change, so these chapters are kept "
                        "indefinitely. Delete this directory to refetch."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    def _path(self, name: str) -> Path | None:
        if self.translation_dir is None:
            return None
        return self.translation_dir / name

    def get_books(self) -> Any | None:
        if self.translation_dir is None:
            return None
        path = self._path("books.json")
        if not path.is_file():
            return None
        payload = _read_json(path)
        books = payload.get("books") if isinstance(payload, dict) else None
        if isinstance(books, list) and books:
            self.stats.books_hits = 1
            return books
        return None

    def put_books(self, books: Any) -> None:
        path = self._path("books.json")
        if path is None:
            return
        _atomic_write(
            path,
            json.dumps(
                {"kind": "jarvis.bible.provider-cache.books", "translation_id": self.translation_id,
                 "saved_at": _now(), "books": books},
                ensure_ascii=False,
            ),
        )

    def get_chapter(self, chapter_id: str) -> Any | None:
        """The cached provider payload for a chapter, or ``None`` for a miss.

        A hit must be for the chapter that was asked for and must actually carry
        something. A file that is truncated, empty or left over from a different
        chapter id counts as a miss rather than as an answer, so a killed run can
        never be mistaken for a complete one.
        """
        path = self.chapter_path(chapter_id)
        if path is None or not path.is_file():
            return None
        payload = _read_json(path)
        if not isinstance(payload, dict):
            return None
        if str(payload.get("chapter") or "") != chapter_id:
            return None
        stored = payload.get("payload")
        if stored is None or stored == "" or stored == [] or stored == {}:
            return None
        self.stats.chapter_hits += 1
        return stored

    def put_chapter(self, chapter_id: str, payload: Any) -> None:
        """Keep a chapter's provider payload exactly as it arrived.

        The payload is stored raw rather than as parsed verses. Scripture text
        does not change, but the way a provider frames it does -- a heading can be
        tagged with a verse number -- so keeping the bytes means a parser fix can
        be applied to a warm cache without spending a single call again.
        """
        path = self.chapter_path(chapter_id)
        if path is None:
            return
        if payload is None or payload == "" or payload == [] or payload == {}:
            raise ValueError(
                f"refusing to cache an empty payload for {chapter_id!r}: an empty entry "
                "would read as a successful fetch that returned nothing"
            )
        _atomic_write(
            path,
            json.dumps(
                {"kind": "jarvis.bible.provider-cache.chapter", "chapter": chapter_id,
                 "dialect": CACHE_VERSION, "saved_at": _now(), "payload": payload},
                ensure_ascii=False,
            ),
        )
        self.stats.chapter_writes += 1

    def chapter_path(self, chapter_id: str) -> Path | None:
        """Where a chapter would live, without touching the disk."""
        return self._path(f"{_SAFE.sub('-', chapter_id)}.json")

    def cached_chapters(self, chapter_ids: list[str]) -> int:
        """How many of these chapters are already on disk.

        This only counts files that exist; whether each one is *valid* is decided
        by :meth:`get_chapter`. Counting the unverified files is deliberate: an
        estimate is about cost, and a corrupt file costs one call either way.
        """
        return sum(1 for path in (self.chapter_path(c) for c in chapter_ids) if path is not None and path.is_file())

    def missing_chapters(self, chapter_ids: list[str]) -> list[str]:
        """The chapters a fetch would still have to ask the provider for."""
        return [
            chapter_id
            for chapter_id in chapter_ids
            if not (path := self.chapter_path(chapter_id)) or not path.is_file()
        ]

    def get_name(self) -> str:
        """The translation's display name, if it was learned before."""
        payload = _read_json(self._path("name.json")) if self._path("name.json") else None
        if isinstance(payload, dict):
            name = str(payload.get("name") or "").strip()
            if name:
                self.stats.books_hits = 1
                return name
        return ""

    def put_name(self, name: str) -> None:
        """Remember the display name so a warm cache needs no lookup for it either."""
        path = self._path("name.json")
        if path is None or not str(name or "").strip():
            return
        _atomic_write(
            path,
            json.dumps(
                {"kind": "jarvis.bible.provider-cache.name", "translation_id": self.translation_id,
                 "saved_at": _now(), "name": name},
                ensure_ascii=False,
            ),
        )

    def describe(self) -> str:
        if not self.enabled:
            return f"Provider cache off: {self.reason}."
        return f"Provider cache at {self.stats.directory} (kept indefinitely; delete to refetch)."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f"{path.name}.partial")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
