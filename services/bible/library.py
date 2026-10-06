"""Find Bibles in the family's Nextcloud library and bring one back whole.

The library already exists: a Calibre shelf inside Nextcloud holding the study
Bibles, the print Bibles and the audio. This module does not talk WebDAV
itself -- ``services/storage`` owns that, owns the credentials, and is the only
place they live. Every request here goes through the storage service, so
rotating the Nextcloud password is one edit in one place rather than one per
caller.

A fetch hands back **raw bytes**, which are then staged in the import folder
and handed to the ordinary importers. Nothing here understands a chapter, a
verse or a study note; that is the importers' job, and keeping this module
 ignorant of them is what lets a new file format arrive without touching it.

The library root is the operator's to choose. ``calibre_library_path`` is the
existing setting for "where the Nextcloud library lives", so reusing it means
a server already indexing books for search gets Bible imports for free. Blank
is refused with the name of the setting rather than defaulting to a guess,
because guessing picks the wrong shelf and reports it as an empty library.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from services.bible.importer import SUFFIX_KINDS

log = logging.getLogger("bible.library")

LIST_TIMEOUT_SECONDS = 60.0
FETCH_TIMEOUT_SECONDS = 300.0

LIBRARY_SETTING = "calibre_library_path"

DIRECTORY_LABEL = "Up one level"


class LibraryError(ValueError):
    """The caller asked for something this module will not do (400).

    Distinct from :class:`LibraryUnavailable`, which means the shelf could not
    be reached or read at all (503). A wrong folder is the operator's to fix
    and a flat rejection is not useful to them, so the two stay apart.
    """


class LibraryUnavailable(RuntimeError):
    """The library could not be reached or read (503)."""


@dataclass(frozen=True)
class LibraryEntry:
    """One row of the shelf, already sorted out for the operator."""

    path: str
    name: str
    is_dir: bool
    size: int
    kind: str
    installable: bool
    note: str

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "is_dir": self.is_dir,
            "size": self.size,
            "kind": self.kind,
            "installable": self.installable,
            "note": self.note,
        }


@dataclass(frozen=True)
class LibraryListing:
    """A folder's contents, plus what the caller needs to keep browsing."""

    path: str
    root: str
    parent: str
    entries: list[LibraryEntry]

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "root": self.root,
            "parent": self.parent,
            "entries": [entry.as_dict() for entry in self.entries],
            "count": len(self.entries),
            "installable": sum(1 for entry in self.entries if entry.installable),
        }


def normalise_path(path: str) -> str:
    """Return a rooted, slash-only path with no ``.`` or ``..`` segments.

    The shelf is a flat namespace of strings and a traversal segment would let a
    request leave the library entirely, so the segments are resolved here
    rather than trusted.
    """
    text = str(path or "").strip().replace("\\", "/")
    parts: list[str] = []
    for segment in text.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    return "/" + "/".join(parts)


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _kind_of(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return SUFFIX_KINDS.get(suffix, "")


def describe(raw: list[dict], *, path: str, root: str) -> LibraryListing:
    """Turn the storage service's entries into a browsable listing.

    Files this module cannot import are kept and marked rather than dropped.
    A shelf holding one unreadable DOCX should not look like a shelf with a
    hole in it, and the reason belongs next to the file rather than in a log
    line an operator never sees.
    """
    entries: list[LibraryEntry] = []
    for item in raw:
        item_path = str(item.get("path") or "")
        if not item_path:
            continue
        is_dir = bool(item.get("is_dir"))
        name = str(item.get("name") or item_path.rsplit("/", 1)[-1])
        size = int(item.get("size") or 0)
        kind = "" if is_dir else _kind_of(name)
        installable = bool(kind)
        if is_dir:
            note = "Folder"
        elif installable:
            note = f"Ready to import as a {kind}"
        else:
            note = f"Not a Bible format this service can read ({Path(name).suffix or 'no extension'})"
        entries.append(
            LibraryEntry(
                path=item_path,
                name=name,
                is_dir=is_dir,
                size=size,
                kind=kind,
                installable=installable,
                note=note,
            )
        )

    entries.sort(key=lambda entry: (not entry.is_dir, entry.name.lower()))

    parent = "" if path == root else path.rsplit("/", 1)[0]

    return LibraryListing(path=path, root=root, parent=parent, entries=entries)


def candidate_names(path: str) -> str:
    """The file name an operator would recognise inside the import folder.

    The folder is folded into a short digest rather than the full path because
    two shelves on different Nextcloud folders both called ``Bible.epub`` would
    otherwise overwrite one another, and the full path is long enough to make
    the import folder unreadable. The real name stays at the end so an operator
    can still recognise it.
    """
    name = path.rsplit("/", 1)[-1] or "download"
    folder = path.rsplit("/", 1)[0] if "/" in path else ""
    if not folder:
        return f"nextcloud-{name}"
    digest = hashlib.sha256(folder.encode("utf-8")).hexdigest()[:8]
    return f"nextcloud-{digest}-{name}"


class LibraryClient:
    """Browse the Nextcloud shelf and fetch a file from it.

    Every call needs the storage service's URL; there is no default, because a
    silent fallback here would mean importing from somewhere the operator never
    named.
    """

    def __init__(self, *, storage_url: str, internal_secret: str) -> None:
        self.storage_url = str(storage_url or "").strip().rstrip("/")
        self.internal_secret = str(internal_secret or "")

    def _endpoint(self) -> str:
        if not self.storage_url:
            raise LibraryUnavailable(
                "The storage service URL is not set, so the Nextcloud library "
                "cannot be reached. Set storage_svc_url (or STORAGE_SVC_URL)."
            )
        if not self.internal_secret:
            raise LibraryUnavailable(
                "The internal secret is not set, so the storage service will "
                "refuse the request. Set INTERNAL_SECRET."
            )
        return self.storage_url

    async def browse(self, *, root: str, path: str) -> LibraryListing:
        """List one folder of the shelf.

        An empty path means the shelf root, because that is what the admin page
        asks for first and an empty string is what it has before it knows
        anything.
        """
        base = normalise_path(root)
        if not base or base == "/":
            raise LibraryUnavailable(
                f"{LIBRARY_SETTING} is not set, so there is no shelf to browse. "
                "Point it at the Nextcloud folder holding the Bibles, for "
                "example /Books/Text."
            )
        wanted = normalise_path(path) if str(path).strip() else base
        if not _within(wanted, base):
            raise LibraryError(
                f"{wanted!r} is outside the library folder {base!r}. "
                "Only the configured shelf can be browsed."
            )

        url = f"{self._endpoint()}/providers/list"
        timeout = aiohttp.ClientTimeout(total=LIST_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url,
                    json={
                        "provider": {"kind": "nextcloud", "settings": {}},
                        "path": wanted,
                        "recursive": False,
                    },
                    headers={"X-Internal-Secret": self.internal_secret},
                ) as response:
                    body = await _json(response, url)
        except aiohttp.ClientError as exc:
            raise LibraryUnavailable(
                f"Could not reach the storage service at {url}: {exc}"
            ) from exc

        raw = body.get("entries")
        if not isinstance(raw, list):
            raise LibraryUnavailable(
                f"{url} did not return a list of entries, so the shelf cannot "
                "be shown. It answered with "
                f"{sorted(body)[:6] if isinstance(body, dict) else type(body).__name__}."
            )
        return describe(raw, path=wanted, root=base)

    async def fetch(self, *, root: str, path: str, destination: Path) -> Path:
        """Download one file into ``destination`` and return the path written."""
        wanted = normalise_path(path)
        base = normalise_path(root)
        if not _within(wanted, base) or wanted == base:
            raise LibraryError(
                f"{wanted!r} is not a file inside the library folder {base!r}."
            )
        if not _kind_of(wanted):
            raise LibraryError(
                f"{wanted!r} is not a Bible format this service can read. "
                f"Supported: {', '.join(sorted(SUFFIX_KINDS))}."
            )

        url = f"{self._endpoint()}/providers/fetch"
        timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url,
                    json={
                        "provider": {"kind": "nextcloud", "settings": {}},
                        "path": wanted,
                    },
                    headers={"X-Internal-Secret": self.internal_secret},
                ) as response:
                    body = await _json(response, url)
        except aiohttp.ClientError as exc:
            raise LibraryUnavailable(
                f"Could not reach the storage service at {url}: {exc}"
            ) from exc

        encoded = body.get("content_b64")
        if not isinstance(encoded, str) or not encoded:
            raise LibraryUnavailable(
                f"{url} returned no content for {wanted!r}, so nothing was written."
            )
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise LibraryUnavailable(
                f"{url} returned something that is not base64, so {wanted!r} "
                f"was not written: {exc}"
            ) from exc
        if not payload:
            raise LibraryUnavailable(
                f"{wanted!r} came back empty, so nothing was written."
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        staged = destination.with_suffix(destination.suffix + ".part")
        staged.write_bytes(payload)
        staged.replace(destination)
        log.info("Fetched %s from Nextcloud to %s (%d bytes)", wanted, destination, len(payload))
        return destination


async def _json(response: aiohttp.ClientResponse, url: str) -> dict:
    """Return the body as a dict, turning every failure into a stated reason."""
    if response.status >= 400:
        detail = (await response.text()).strip()
        raise LibraryUnavailable(
            f"{url} answered {response.status}"
            + (f": {detail}" if detail else ".")
        )
    try:
        body = await response.json(content_type=None)
    except Exception as exc:  # noqa: BLE001 - any decoder failure reads the same
        raise LibraryUnavailable(f"{url} did not answer with JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise LibraryUnavailable(
            f"{url} answered with {type(body).__name__}, not an object."
        )
    return body
