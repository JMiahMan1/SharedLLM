"""Two-way file sync between a workspace directory and a Nextcloud folder.

A workspace can keep its files in git, in Nextcloud, or in both. This module is
the Nextcloud half: a small WebDAV client plus a sync engine that reconciles
the local tree with the remote tree using a per-file record of the last state
both sides agreed on (``WorkspaceSyncEntry`` rows).

The engine runs inside workspace_runtime because that is the only service that
mounts the workspace files; the Storage service's ``/providers/mirror`` cannot
see them.

Reconciliation rules for one path, given the last synced record:

* changed on one side only  -> copy it to the other side
* changed on both sides     -> keep the local file, save the remote one next
                               to it as a "conflicted copy", upload both
* deleted on one side and unchanged on the other -> delete it on the other
* deleted on one side but edited on the other    -> the edit wins (restored)
* new on both sides with identical bytes          -> just record it

``push`` and ``pull`` are one-way variants: the source side is authoritative,
and only paths this engine previously synced are ever deleted on the target.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import quote, unquote, urlparse

import requests

log = logging.getLogger("workspace_runtime.nextcloud_sync")

SYNC_DIRECTIONS = ("both", "push", "pull")

# Paths that are never synced, whatever the workspace's own excludes say: git
# metadata is per-clone state (syncing it between machines corrupts repos), and
# partially written transfer files must not travel.
ALWAYS_EXCLUDED = (".git", "*.sharedllm-partial")

_DAV_NS = {"d": "DAV:"}
_PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:resourcetype/><d:getetag/><d:getcontentlength/><d:getlastmodified/>"
    "</d:prop></d:propfind>"
)


class NextcloudSyncError(RuntimeError):
    """A sync could not run or had to stop; the message is operator-facing."""


@dataclass(frozen=True)
class RemoteEntry:
    path: str  # relative to the sync root, POSIX, no leading slash
    is_dir: bool
    etag: str = ""
    size: int = 0


@dataclass(frozen=True)
class LocalEntry:
    path: str
    is_dir: bool
    size: int = 0
    mtime_ns: int = 0


@dataclass
class SyncRecord:
    """The state of one path the last time both sides agreed on it."""

    path: str
    is_dir: bool
    size: int = 0
    mtime_ns: int = 0
    sha256: str = ""
    etag: str = ""


@dataclass
class SyncReport:
    direction: str
    remote_root: str
    dry_run: bool = False
    uploaded: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    deleted_local: list[str] = field(default_factory=list)
    deleted_remote: list[str] = field(default_factory=list)
    created_local_dirs: list[str] = field(default_factory=list)
    created_remote_dirs: list[str] = field(default_factory=list)
    conflicts: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    bytes_uploaded: int = 0
    bytes_downloaded: int = 0

    def as_dict(self) -> dict:
        return {
            "direction": self.direction,
            "remote_root": self.remote_root,
            "dry_run": self.dry_run,
            "uploaded": self.uploaded,
            "downloaded": self.downloaded,
            "deleted_local": self.deleted_local,
            "deleted_remote": self.deleted_remote,
            "created_local_dirs": self.created_local_dirs,
            "created_remote_dirs": self.created_remote_dirs,
            "conflicts": self.conflicts,
            "errors": self.errors,
            "bytes_uploaded": self.bytes_uploaded,
            "bytes_downloaded": self.bytes_downloaded,
            "changed": bool(
                self.uploaded
                or self.downloaded
                or self.deleted_local
                or self.deleted_remote
                or self.created_local_dirs
                or self.created_remote_dirs
            ),
        }


class RemoteStore(Protocol):
    """What the engine needs from a remote. ``WebDavClient`` implements it."""

    def exists(self, path: str) -> bool: ...
    def walk(self, root: str) -> dict[str, RemoteEntry]: ...
    def download(self, path: str, dest: Path) -> None: ...
    def upload(self, path: str, src: Path) -> str: ...
    def mkdir(self, path: str) -> None: ...
    def delete(self, path: str) -> None: ...


# ---------------------------------------------------------------------------
# WebDAV client
# ---------------------------------------------------------------------------


class WebDavClient:
    """Minimal Nextcloud WebDAV client: everything the sync engine needs.

    Every failure raises ``NextcloudSyncError``. A listing error must never
    look like an empty folder, because the engine would read that as "every
    file was deleted remotely" and delete the local copies.
    """

    def __init__(self, url: str, username: str, password: str, timeout_seconds: float):
        if not (url and username and password):
            raise NextcloudSyncError("Nextcloud url, username and password are all required")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise NextcloudSyncError(f"Nextcloud url is not an http(s) URL: {url!r}")
        # base_path is kept UNQUOTED: PROPFIND hrefs are unquoted before they
        # are compared with it, and _url() quotes the whole path once.
        dav_prefix = f"/remote.php/dav/files/{username}"
        base_path = unquote(parsed.path).rstrip("/")
        # Accept either the server URL or a full DAV URL for this user.
        if not base_path.startswith(dav_prefix):
            base_path = dav_prefix + base_path
        self.base_path = base_path
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self.timeout = timeout_seconds
        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.headers["User-Agent"] = "SharedLLM-WorkspaceSync/1.0"

    def _url(self, path: str) -> str:
        clean = str(PurePosixPath("/" + path.strip("/")))
        return f"{self.base_url}{quote(self.base_path + clean, safe='/')}"

    def _request(self, method: str, path: str, ok: Iterable[int], **kwargs) -> requests.Response:
        try:
            resp = self.session.request(method, self._url(path), timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise NextcloudSyncError(f"Nextcloud {method} {path} failed: {exc}") from exc
        if resp.status_code not in set(ok):
            detail = resp.text[:300] if resp.text else ""
            raise NextcloudSyncError(f"Nextcloud {method} {path} returned HTTP {resp.status_code}: {detail}".strip())
        return resp

    def _propfind(self, path: str, depth: str) -> list[tuple[str, RemoteEntry]]:
        resp = self._request(
            "PROPFIND",
            path,
            ok=(207,),
            data=_PROPFIND_BODY,
            headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
        )
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise NextcloudSyncError(f"Nextcloud PROPFIND {path} returned invalid XML: {exc}") from exc
        out: list[tuple[str, RemoteEntry]] = []
        for response in root.findall("d:response", _DAV_NS):
            href = response.findtext("d:href", default="", namespaces=_DAV_NS)
            abs_path = unquote(urlparse(href).path)
            prop = None
            for propstat in response.findall("d:propstat", _DAV_NS):
                status = propstat.findtext("d:status", default="", namespaces=_DAV_NS)
                if " 200 " in f"{status} ":
                    prop = propstat.find("d:prop", _DAV_NS)
                    break
            if prop is None:
                continue
            is_dir = prop.find("d:resourcetype/d:collection", _DAV_NS) is not None
            etag = (prop.findtext("d:getetag", default="", namespaces=_DAV_NS) or "").strip('"')
            size_text = prop.findtext("d:getcontentlength", default="", namespaces=_DAV_NS) or "0"
            try:
                size = int(size_text)
            except ValueError:
                size = 0
            out.append((abs_path, RemoteEntry(path="", is_dir=is_dir, etag=etag, size=size)))
        return out

    def exists(self, path: str) -> bool:
        try:
            resp = self.session.request(
                "PROPFIND",
                self._url(path),
                timeout=self.timeout,
                data=_PROPFIND_BODY,
                headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
            )
        except requests.RequestException as exc:
            raise NextcloudSyncError(f"Nextcloud PROPFIND {path} failed: {exc}") from exc
        if resp.status_code == 404:
            return False
        if resp.status_code != 207:
            raise NextcloudSyncError(f"Nextcloud PROPFIND {path} returned HTTP {resp.status_code}")
        return True

    def walk(self, root: str) -> dict[str, RemoteEntry]:
        """Every file and folder under ``root``, keyed by root-relative path.

        Nextcloud refuses ``Depth: infinity`` by default, so this walks one
        level at a time.
        """
        root_abs = self.base_path + str(PurePosixPath("/" + root.strip("/"))).rstrip("/")
        entries: dict[str, RemoteEntry] = {}
        pending = [""]
        while pending:
            rel_dir = pending.pop()
            for abs_path, entry in self._propfind(_join(root, rel_dir), "1"):
                if not abs_path.startswith(root_abs):
                    continue
                rel = abs_path[len(root_abs):].strip("/")
                if rel == rel_dir:
                    continue  # the folder itself
                entries[rel] = RemoteEntry(path=rel, is_dir=entry.is_dir, etag=entry.etag, size=entry.size)
                if entry.is_dir:
                    pending.append(rel)
        return entries

    def download(self, path: str, dest: Path) -> None:
        resp = self._request("GET", path, ok=(200,), stream=True)
        try:
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
        finally:
            resp.close()

    def upload(self, path: str, src: Path) -> str:
        """PUT a file and return its new etag."""
        mtime = int(src.stat().st_mtime)
        with src.open("rb") as fh:
            resp = self._request(
                "PUT",
                path,
                ok=(200, 201, 204),
                data=fh,
                # Nextcloud keeps the client's modification time when told it.
                headers={"Content-Type": "application/octet-stream", "X-OC-Mtime": str(mtime)},
            )
        etag = (resp.headers.get("OC-ETag") or resp.headers.get("ETag") or "").strip('"')
        if etag:
            return etag
        # Some proxies strip the ETag header; ask for it.
        listing = self._propfind(path, "0")
        if not listing:
            raise NextcloudSyncError(f"Nextcloud did not report an etag for {path} after upload")
        return listing[0][1].etag

    def mkdir(self, path: str) -> None:
        # 405 = the collection already exists.
        self._request("MKCOL", path, ok=(201, 405))

    def delete(self, path: str) -> None:
        self._request("DELETE", path, ok=(200, 204, 404))


def _join(root: str, rel: str) -> str:
    root = "/" + root.strip("/")
    rel = rel.strip("/")
    if not rel:
        return root
    return f"{root.rstrip('/')}/{rel}"


# ---------------------------------------------------------------------------
# Local tree
# ---------------------------------------------------------------------------


def is_excluded(rel_path: str, patterns: Iterable[str]) -> bool:
    """True when any path segment, or the whole path, matches a pattern.

    Patterns are shell globs (``node_modules``, ``*.log``, ``build/*``).
    """
    parts = PurePosixPath(rel_path).parts
    for pattern in patterns:
        pattern = pattern.strip().strip("/")
        if not pattern:
            continue
        if fnmatch.fnmatchcase(rel_path, pattern):
            return True
        if "/" not in pattern and any(fnmatch.fnmatchcase(part, pattern) for part in parts):
            return True
    return False


def scan_local(root: Path, excludes: Iterable[str]) -> dict[str, LocalEntry]:
    patterns = list(ALWAYS_EXCLUDED) + list(excludes)
    entries: dict[str, LocalEntry] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        rel_base = base.relative_to(root).as_posix()
        rel_base = "" if rel_base == "." else rel_base
        kept = []
        for name in sorted(dirnames):
            rel = f"{rel_base}/{name}" if rel_base else name
            full = base / name
            if full.is_symlink() or is_excluded(rel, patterns):
                continue
            kept.append(name)
            entries[rel] = LocalEntry(path=rel, is_dir=True)
        dirnames[:] = kept
        for name in sorted(filenames):
            rel = f"{rel_base}/{name}" if rel_base else name
            full = base / name
            if full.is_symlink() or is_excluded(rel, patterns):
                continue
            st = full.stat()
            entries[rel] = LocalEntry(path=rel, is_dir=False, size=st.st_size, mtime_ns=st.st_mtime_ns)
    return entries


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def conflict_name(rel_path: str, when: datetime) -> str:
    p = PurePosixPath(rel_path)
    stamp = when.strftime("%Y-%m-%d %H%M%S")
    name = f"{p.stem} (conflicted copy {stamp}){p.suffix}"
    return str(p.with_name(name)) if str(p.parent) != "." else name


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class SyncEngine:
    def __init__(
        self,
        local_root: Path,
        remote: RemoteStore,
        remote_root: str,
        records: dict[str, SyncRecord],
        excludes: Iterable[str] = (),
        now: datetime | None = None,
    ):
        self.local_root = local_root
        self.remote = remote
        self.remote_root = "/" + remote_root.strip("/")
        self.records = dict(records)
        self.excludes = list(excludes)
        self.now = now or datetime.now(UTC)

    # -- helpers ---------------------------------------------------------

    def _local(self, rel: str) -> Path:
        target = (self.local_root / rel).resolve()
        target.relative_to(self.local_root.resolve())  # refuses traversal from remote names
        return target

    def _remote(self, rel: str) -> str:
        return _join(self.remote_root, rel)

    def _local_changed(self, entry: LocalEntry, rec: SyncRecord | None) -> bool:
        if rec is None or rec.is_dir:
            return True
        if entry.size == rec.size and entry.mtime_ns == rec.mtime_ns:
            return False
        # Touched but maybe not edited (git checkout, copy): compare content.
        return sha256_file(self._local(entry.path)) != rec.sha256

    @staticmethod
    def _remote_changed(entry: RemoteEntry, rec: SyncRecord | None) -> bool:
        return rec is None or rec.is_dir or not entry.etag or entry.etag != rec.etag

    def _record_file(self, rel: str, etag: str) -> None:
        local = self._local(rel)
        st = local.stat()
        self.records[rel] = SyncRecord(
            path=rel, is_dir=False, size=st.st_size, mtime_ns=st.st_mtime_ns,
            sha256=sha256_file(local), etag=etag,
        )

    def _ensure_remote_parents(self, rel: str, remote_dirs: set[str], report: SyncReport) -> None:
        parts = PurePosixPath(rel).parts[:-1]
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            if d in remote_dirs:
                continue
            if not report.dry_run:
                self.remote.mkdir(self._remote(d))
            remote_dirs.add(d)
            report.created_remote_dirs.append(d)
            self.records[d] = SyncRecord(path=d, is_dir=True)

    def _upload(self, rel: str, remote_dirs: set[str], report: SyncReport) -> None:
        self._ensure_remote_parents(rel, remote_dirs, report)
        size = self._local(rel).stat().st_size
        if not report.dry_run:
            etag = self.remote.upload(self._remote(rel), self._local(rel))
            self._record_file(rel, etag)
        report.uploaded.append(rel)
        report.bytes_uploaded += size

    def _download(self, entry: RemoteEntry, report: SyncReport, dest_rel: str | None = None) -> None:
        dest_rel = dest_rel or entry.path
        if not report.dry_run:
            dest = self._local(dest_rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Download beside the target and rename, so a dropped connection
            # never leaves a truncated file where a good one was.
            fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".sharedllm-partial")
            os.close(fd)
            tmp = Path(tmp_name)
            try:
                self.remote.download(self._remote(entry.path), tmp)
                os.replace(tmp, dest)
            finally:
                tmp.unlink(missing_ok=True)
            if dest_rel == entry.path:
                self._record_file(dest_rel, entry.etag)
        report.downloaded.append(dest_rel)
        report.bytes_downloaded += entry.size

    def _delete_local(self, rel: str, report: SyncReport) -> None:
        if not report.dry_run:
            self._local(rel).unlink(missing_ok=True)
        self.records.pop(rel, None)
        report.deleted_local.append(rel)

    def _delete_remote(self, rel: str, report: SyncReport) -> None:
        if not report.dry_run:
            self.remote.delete(self._remote(rel))
        self.records.pop(rel, None)
        report.deleted_remote.append(rel)

    def _same_content(self, entry: RemoteEntry) -> bool:
        """New on both sides: download the remote copy and compare bytes."""
        local = self._local(entry.path)
        if local.stat().st_size != entry.size:
            return False
        fd, tmp_name = tempfile.mkstemp(dir=local.parent, prefix=f".{local.name}.", suffix=".sharedllm-partial")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            self.remote.download(self._remote(entry.path), tmp)
            return sha256_file(tmp) == sha256_file(local)
        finally:
            tmp.unlink(missing_ok=True)

    # -- main ------------------------------------------------------------

    def run(self, direction: str = "both", dry_run: bool = False) -> SyncReport:
        if direction not in SYNC_DIRECTIONS:
            raise NextcloudSyncError(f"Unknown sync direction {direction!r}; expected one of {SYNC_DIRECTIONS}")
        report = SyncReport(direction=direction, remote_root=self.remote_root, dry_run=dry_run)

        self.local_root.mkdir(parents=True, exist_ok=True)
        remote_exists = self.remote.exists(self.remote_root)
        if not remote_exists:
            if self.records and direction != "push":
                # The folder was synced before and is now gone. Treating that
                # as "the user deleted every file" would wipe the workspace.
                raise NextcloudSyncError(
                    f"Nextcloud folder {self.remote_root} no longer exists. Refusing to sync, because that "
                    "would delete every local file. Restore the folder, or push to recreate it."
                )
            if not dry_run:
                self._mkdirs_remote_root()
            raw_remote: dict[str, RemoteEntry] = {}
        else:
            raw_remote = self.remote.walk(self.remote_root)
        patterns = list(ALWAYS_EXCLUDED) + self.excludes
        remote = {rel: entry for rel, entry in raw_remote.items() if not is_excluded(rel, patterns)}

        local = scan_local(self.local_root, self.excludes)
        remote_dirs = {rel for rel, e in remote.items() if e.is_dir}

        file_paths = sorted(
            {p for p, e in local.items() if not e.is_dir}
            | {p for p, e in remote.items() if not e.is_dir}
            | {p for p, r in self.records.items() if not r.is_dir}
        )
        for rel in file_paths:
            try:
                self._sync_file(rel, local.get(rel), remote.get(rel), direction, remote_dirs, report)
            except NextcloudSyncError as exc:
                report.errors.append({"path": rel, "error": str(exc)})
            except OSError as exc:
                report.errors.append({"path": rel, "error": f"local filesystem error: {exc}"})
            except ValueError:
                report.errors.append({"path": rel, "error": "path escapes the workspace; skipped"})

        self._sync_dirs(local, remote, raw_remote, remote_dirs, direction, report)
        return report

    def _mkdirs_remote_root(self) -> None:
        parts = PurePosixPath(self.remote_root).parts[1:]
        for i in range(1, len(parts) + 1):
            self.remote.mkdir("/" + "/".join(parts[:i]))

    def _sync_file(
        self,
        rel: str,
        loc: LocalEntry | None,
        rem: RemoteEntry | None,
        direction: str,
        remote_dirs: set[str],
        report: SyncReport,
    ) -> None:
        rec = self.records.get(rel)
        if rec is not None and rec.is_dir:
            rec = None
        # A file on one side and a folder on the other cannot be reconciled.
        if (loc and loc.is_dir) or (rem and rem.is_dir):
            report.errors.append({"path": rel, "error": "is a file on one side and a folder on the other"})
            return

        if loc and rem:
            l_changed = self._local_changed(loc, rec)
            r_changed = self._remote_changed(rem, rec)
            if direction == "push":
                if l_changed or r_changed:
                    self._upload(rel, remote_dirs, report)
                return
            if direction == "pull":
                if l_changed or r_changed:
                    self._download(rem, report)
                return
            if not l_changed and not r_changed:
                return
            if l_changed and not r_changed:
                self._upload(rel, remote_dirs, report)
                return
            if r_changed and not l_changed:
                self._download(rem, report)
                return
            # Both sides changed (or both are new): identical bytes are fine.
            if self._same_content(rem):
                if not report.dry_run:
                    self._record_file(rel, rem.etag)
                return
            copy_rel = conflict_name(rel, self.now)
            self._download(rem, report, dest_rel=copy_rel)
            self._upload(rel, remote_dirs, report)
            if not report.dry_run:
                self._upload(copy_rel, remote_dirs, report)
            report.conflicts.append({"path": rel, "remote_copy": copy_rel})
            return

        if loc and not rem:
            if direction == "push":
                self._upload(rel, remote_dirs, report)
            elif direction == "pull":
                if rec is not None:  # pull only removes what an earlier sync brought
                    self._delete_local(rel, report)
            elif rec is None:
                self._upload(rel, remote_dirs, report)
            elif self._local_changed(loc, rec):
                self._upload(rel, remote_dirs, report)  # edited here, deleted there: keep the edit
            else:
                self._delete_local(rel, report)
            return

        if rem and not loc:
            if direction == "pull":
                self._download(rem, report)
            elif direction == "push":
                if rec is not None:  # push only removes what an earlier sync sent
                    self._delete_remote(rel, report)
            elif rec is None or self._remote_changed(rem, rec):
                self._download(rem, report)
            else:
                self._delete_remote(rel, report)
            return

        # Gone on both sides.
        self.records.pop(rel, None)

    def _sync_dirs(
        self,
        local: dict[str, LocalEntry],
        remote: dict[str, RemoteEntry],
        raw_remote: dict[str, RemoteEntry],
        remote_dirs: set[str],
        direction: str,
        report: SyncReport,
    ) -> None:
        local_dirs = {p for p, e in local.items() if e.is_dir}
        known = {p for p, r in self.records.items() if r.is_dir}
        all_dirs = local_dirs | {p for p, e in remote.items() if e.is_dir} | known

        # Create parents before children.
        for rel in sorted(all_dirs, key=lambda p: p.count("/")):
            in_local, in_remote, in_rec = rel in local_dirs, rel in remote_dirs, rel in known
            if in_local and in_remote:
                self.records[rel] = SyncRecord(path=rel, is_dir=True)
            elif in_local and direction != "pull" and (not in_rec or direction == "push"):
                if not report.dry_run:
                    self.remote.mkdir(self._remote(rel))
                remote_dirs.add(rel)
                report.created_remote_dirs.append(rel)
                self.records[rel] = SyncRecord(path=rel, is_dir=True)
            elif in_remote and direction != "push" and (not in_rec or direction == "pull"):
                if not report.dry_run:
                    self._local(rel).mkdir(parents=True, exist_ok=True)
                report.created_local_dirs.append(rel)
                self.records[rel] = SyncRecord(path=rel, is_dir=True)

        # Folders deleted on one side: remove them on the other once empty.
        # Deepest first, and never a folder that still holds anything.
        for rel in sorted(all_dirs, key=lambda p: p.count("/"), reverse=True):
            in_local, in_remote, in_rec = rel in local_dirs, rel in remote_dirs, rel in known
            if not in_rec:
                continue
            if in_local and not in_remote and direction in {"both", "pull"}:
                target = self._local(rel)
                if not report.dry_run:
                    if target.is_dir() and not any(target.iterdir()):
                        target.rmdir()
                    else:
                        continue
                self.records.pop(rel, None)
                report.deleted_local.append(rel + "/")
            elif in_remote and not in_local and direction in {"both", "push"}:
                # WebDAV DELETE is recursive, so only delete a folder that is
                # empty now - including of excluded files we never synced.
                prefix = rel + "/"
                gone = {p.rstrip("/") for p in report.deleted_remote}
                still_used = (
                    any(p.startswith(prefix) for p in self.records)
                    or any(p.startswith(prefix) for p in report.uploaded)
                    or any(p.startswith(prefix) and p not in gone for p in raw_remote)
                )
                if still_used:
                    continue
                if not report.dry_run:
                    self.remote.delete(self._remote(rel))
                self.records.pop(rel, None)
                report.deleted_remote.append(rel + "/")
            elif not in_local and not in_remote:
                self.records.pop(rel, None)
