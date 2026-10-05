"""Sync engine and WebDAV client for Nextcloud-backed workspaces."""

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

from services.workspace_runtime.nextcloud_sync import (
    NextcloudSyncError,
    RemoteEntry,
    SyncEngine,
    WebDavClient,
    conflict_name,
    is_excluded,
)

ROOT = "/Code/Proj"
NOW = datetime(2026, 10, 5, 12, 30, 0, tzinfo=UTC)


class FakeRemote:
    """In-memory Nextcloud: absolute path -> bytes (files) or None (folders)."""

    def __init__(self):
        self.items: dict[str, bytes | None] = {}
        self.etags: dict[str, int] = {}
        self.fail_walk = False

    def _bump(self, path):
        self.etags[path] = self.etags.get(path, 0) + 1

    def put(self, path: str, data: bytes):
        parts = path.strip("/").split("/")
        for i in range(1, len(parts)):
            self.items.setdefault("/" + "/".join(parts[:i]), None)
        self.items[path] = data
        self._bump(path)

    def exists(self, path):
        return path in self.items

    def walk(self, root):
        if self.fail_walk:
            raise NextcloudSyncError("PROPFIND failed")
        prefix = root.rstrip("/") + "/"
        out = {}
        for path, data in self.items.items():
            if path.startswith(prefix):
                rel = path[len(prefix):]
                if data is None:
                    out[rel] = RemoteEntry(path=rel, is_dir=True)
                else:
                    out[rel] = RemoteEntry(path=rel, is_dir=False, etag=str(self.etags[path]), size=len(data))
        return out

    def download(self, path, dest: Path):
        dest.write_bytes(self.items[path])

    def upload(self, path, src: Path):
        self.items[path] = src.read_bytes()
        self._bump(path)
        return str(self.etags[path])

    def mkdir(self, path):
        self.items.setdefault(path, None)

    def delete(self, path):
        for p in [p for p in self.items if p == path or p.startswith(path + "/")]:
            del self.items[p]


@pytest.fixture
def local():
    # Scratch lives under the repo's .tmp/, never the system temp dir.
    path = Path(".tmp/test-nextcloud-sync") / uuid.uuid4().hex
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


class Harness:
    def __init__(self, local: Path, excludes=()):
        self.local = local
        self.remote = FakeRemote()
        self.records = {}
        self.excludes = list(excludes)

    def sync(self, direction="both", dry_run=False):
        engine = SyncEngine(self.local, self.remote, ROOT, self.records, self.excludes, now=NOW)
        report = engine.run(direction, dry_run=dry_run)
        if not dry_run:
            self.records = engine.records
        return report

    def write(self, rel, data: bytes):
        p = self.local / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def r(self, rel):
        return self.remote.items.get(f"{ROOT}/{rel}")


def test_first_sync_uploads_local_files_and_creates_remote_folder(local):
    h = Harness(local)
    h.write("a.txt", b"A")
    h.write("docs/b.bin", b"\x00\x01\x02")
    report = h.sync()
    assert sorted(report.uploaded) == ["a.txt", "docs/b.bin"]
    assert h.r("a.txt") == b"A"
    assert h.r("docs/b.bin") == b"\x00\x01\x02"  # binary survives
    assert h.r("docs") is None and f"{ROOT}/docs" in h.remote.items


def test_first_sync_downloads_remote_files(local):
    h = Harness(local)
    h.remote.put(f"{ROOT}/notes/n.md", b"# hi")
    report = h.sync()
    assert report.downloaded == ["notes/n.md"]
    assert (local / "notes/n.md").read_bytes() == b"# hi"
    # nothing left behind from the atomic download
    assert not list(local.rglob("*.sharedllm-partial"))


def test_second_sync_with_no_changes_does_nothing(local):
    h = Harness(local)
    h.write("a.txt", b"A")
    h.remote.put(f"{ROOT}/b.txt", b"B")
    h.sync()
    report = h.sync()
    assert not report.as_dict()["changed"]
    assert report.conflicts == []


def test_edits_flow_both_ways(local):
    h = Harness(local)
    h.write("a.txt", b"A")
    h.remote.put(f"{ROOT}/b.txt", b"B")
    h.sync()
    h.write("a.txt", b"A2")
    h.remote.put(f"{ROOT}/b.txt", b"B2")
    report = h.sync()
    assert report.uploaded == ["a.txt"]
    assert report.downloaded == ["b.txt"]
    assert h.r("a.txt") == b"A2"
    assert (local / "b.txt").read_bytes() == b"B2"


def test_touched_but_unchanged_file_is_not_uploaded(local):
    h = Harness(local)
    h.write("a.txt", b"A")
    h.sync()
    h.write("a.txt", b"A")  # new mtime, same bytes (e.g. git checkout)
    assert h.sync().uploaded == []


def test_deletions_propagate_both_ways(local):
    h = Harness(local)
    h.write("gone-here.txt", b"1")
    h.write("gone-there.txt", b"2")
    h.sync()
    (local / "gone-here.txt").unlink()
    h.remote.delete(f"{ROOT}/gone-there.txt")
    report = h.sync()
    assert report.deleted_remote == ["gone-here.txt"]
    assert report.deleted_local == ["gone-there.txt"]
    assert h.r("gone-here.txt") is None and f"{ROOT}/gone-here.txt" not in h.remote.items
    assert not (local / "gone-there.txt").exists()


def test_edit_beats_delete(local):
    h = Harness(local)
    h.write("keep.txt", b"v1")
    h.sync()
    h.write("keep.txt", b"v2-local-edit")
    h.remote.delete(f"{ROOT}/keep.txt")
    report = h.sync()
    assert report.uploaded == ["keep.txt"]
    assert h.r("keep.txt") == b"v2-local-edit"


def test_conflict_keeps_both_versions_on_both_sides(local):
    h = Harness(local)
    h.write("doc.md", b"base")
    h.sync()
    h.write("doc.md", b"local edit")
    h.remote.put(f"{ROOT}/doc.md", b"remote edit")
    report = h.sync()
    copy = conflict_name("doc.md", NOW)
    assert copy == "doc (conflicted copy 2026-10-05 123000).md"
    assert report.conflicts == [{"path": "doc.md", "remote_copy": copy}]
    assert (local / "doc.md").read_bytes() == b"local edit"
    assert (local / copy).read_bytes() == b"remote edit"
    assert h.r("doc.md") == b"local edit"
    assert h.r(copy) == b"remote edit"
    # and it settles
    assert not h.sync().as_dict()["changed"]


def test_new_on_both_sides_with_same_bytes_is_not_a_conflict(local):
    h = Harness(local)
    h.write("same.txt", b"identical")
    h.remote.put(f"{ROOT}/same.txt", b"identical")
    report = h.sync()
    assert report.conflicts == [] and report.uploaded == [] and report.downloaded == []


def test_git_metadata_and_excludes_never_travel(local):
    h = Harness(local, excludes=["node_modules", "*.log", "build/*"])
    h.write(".git/HEAD", b"ref: refs/heads/main")
    h.write("node_modules/x/index.js", b"x")
    h.write("debug.log", b"x")
    h.write("build/out.o", b"x")
    h.write("src/main.py", b"print()")
    h.remote.put(f"{ROOT}/.git/config", b"remote git config")
    report = h.sync()
    assert report.uploaded == ["src/main.py"]
    assert report.downloaded == []
    assert not (local / ".git/config").exists()


def test_push_never_downloads_and_pull_never_uploads(local):
    h = Harness(local)
    h.write("mine.txt", b"m")
    h.remote.put(f"{ROOT}/theirs.txt", b"t")
    report = h.sync("push")
    assert report.uploaded == ["mine.txt"] and report.downloaded == []
    assert not (local / "theirs.txt").exists()

    h2 = Harness(local / "other")
    h2.write("mine.txt", b"m")
    h2.remote.put(f"{ROOT}/theirs.txt", b"t")
    report = h2.sync("pull")
    assert report.downloaded == ["theirs.txt"] and report.uploaded == []
    assert h2.r("mine.txt") is None


def test_push_overwrites_remote_edits(local):
    h = Harness(local)
    h.write("a.txt", b"local")
    h.sync("push")
    h.remote.put(f"{ROOT}/a.txt", b"someone else")
    h.sync("push")
    assert h.r("a.txt") == b"local"


def test_dry_run_changes_nothing(local):
    h = Harness(local)
    h.write("a.txt", b"A")
    h.remote.put(f"{ROOT}/b.txt", b"B")
    before = dict(h.remote.items)
    report = h.sync(dry_run=True)
    assert report.uploaded == ["a.txt"] and report.downloaded == ["b.txt"]
    assert h.remote.items == before
    assert not (local / "b.txt").exists()
    assert h.records == {}


def test_missing_remote_folder_after_a_sync_refuses_instead_of_wiping(local):
    h = Harness(local)
    h.write("precious.txt", b"!")
    h.sync()
    h.remote.items.clear()
    with pytest.raises(NextcloudSyncError, match="no longer exists"):
        h.sync()
    assert (local / "precious.txt").exists()


def test_listing_failure_is_an_error_not_an_empty_folder(local):
    h = Harness(local)
    h.write("precious.txt", b"!")
    h.sync()
    h.remote.fail_walk = True
    with pytest.raises(NextcloudSyncError):
        h.sync()
    assert (local / "precious.txt").exists()


def test_folders_are_created_and_removed(local):
    h = Harness(local)
    (local / "empty-here").mkdir()
    h.remote.mkdir(f"{ROOT}")
    h.remote.mkdir(f"{ROOT}/empty-there")
    h.sync()
    assert f"{ROOT}/empty-here" in h.remote.items
    assert (local / "empty-there").is_dir()

    (local / "empty-here").rmdir()
    h.remote.delete(f"{ROOT}/empty-there")
    h.sync()
    assert f"{ROOT}/empty-here" not in h.remote.items
    assert not (local / "empty-there").exists()


def test_locally_deleted_folder_is_kept_remotely_while_it_holds_excluded_files(local):
    h = Harness(local, excludes=["node_modules"])
    h.write("pkg/index.js", b"x")
    h.sync()
    h.remote.put(f"{ROOT}/pkg/node_modules/dep.js", b"not ours")
    shutil.rmtree(local / "pkg")
    h.sync()
    assert h.r("pkg/index.js") is None and f"{ROOT}/pkg/index.js" not in h.remote.items
    assert h.r("pkg/node_modules/dep.js") == b"not ours"


def test_unknown_direction_is_rejected(local):
    with pytest.raises(NextcloudSyncError, match="Unknown sync direction"):
        Harness(local).sync("sideways")


def test_is_excluded_matches_segments_and_globs():
    assert is_excluded("a/node_modules/b.js", ["node_modules"])
    assert is_excluded("x.log", ["*.log"])
    assert is_excluded("build/out.o", ["build/*"])
    assert not is_excluded("src/build.py", ["build/*"])
    assert not is_excluded("src/main.py", ["node_modules", "*.log"])


# ---------------------------------------------------------------------------
# WebDavClient against a scripted HTTP session
# ---------------------------------------------------------------------------


def _multistatus(*responses):
    body = "".join(
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>"
        f"<d:resourcetype>{'<d:collection/>' if is_dir else ''}</d:resourcetype>"
        f"<d:getetag>&quot;{etag}&quot;</d:getetag><d:getcontentlength>{size}</d:getcontentlength>"
        f"</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
        for href, is_dir, etag, size in responses
    )
    return f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">{body}</d:multistatus>'.encode()


class ScriptedSession:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []
        self.auth = None
        self.headers = {}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("headers", {}).get("Depth")))
        status, content = self.routes.get((method, url), (404, b""))
        resp = requests.Response()
        resp.status_code = status
        resp._content = content
        return resp


BASE = "https://cloud.example.com/remote.php/dav/files/jo%40example.com"
HREF = "/remote.php/dav/files/jo%40example.com"


def test_webdav_walk_recurses_and_handles_email_usernames():
    client = WebDavClient("https://cloud.example.com", "jo@example.com", "pw", timeout_seconds=5)
    client.session = ScriptedSession({
        ("PROPFIND", f"{BASE}/Code/My%20Proj"): (207, _multistatus(
            (f"{HREF}/Code/My%20Proj/", True, "d0", 0),
            (f"{HREF}/Code/My%20Proj/read%20me.md", False, "e1", 5),
            (f"{HREF}/Code/My%20Proj/sub/", True, "d1", 0),
        )),
        ("PROPFIND", f"{BASE}/Code/My%20Proj/sub"): (207, _multistatus(
            (f"{HREF}/Code/My%20Proj/sub/", True, "d1", 0),
            (f"{HREF}/Code/My%20Proj/sub/x.bin", False, "e2", 9),
        )),
    })
    entries = client.walk("/Code/My Proj")
    assert entries == {
        "read me.md": RemoteEntry("read me.md", False, "e1", 5),
        "sub": RemoteEntry("sub", True, "d1", 0),
        "sub/x.bin": RemoteEntry("sub/x.bin", False, "e2", 9),
    }
    assert all(depth == "1" for _, _, depth in client.session.calls)


def test_webdav_errors_raise():
    client = WebDavClient("https://cloud.example.com", "jo@example.com", "pw", timeout_seconds=5)
    client.session = ScriptedSession({("PROPFIND", f"{BASE}/Code"): (401, b"nope")})
    with pytest.raises(NextcloudSyncError, match="HTTP 401"):
        client.walk("/Code")


def test_webdav_exists_distinguishes_missing_from_broken():
    client = WebDavClient("https://cloud.example.com", "jo@example.com", "pw", timeout_seconds=5)
    client.session = ScriptedSession({
        ("PROPFIND", f"{BASE}/there"): (207, _multistatus((f"{HREF}/there/", True, "d", 0))),
        ("PROPFIND", f"{BASE}/broken"): (500, b""),
    })
    assert client.exists("/there") is True
    assert client.exists("/missing") is False
    with pytest.raises(NextcloudSyncError):
        client.exists("/broken")


@pytest.mark.parametrize("url,user,pw", [("", "u", "p"), ("https://x", "", "p"), ("https://x", "u", ""), ("ftp://x", "u", "p")])
def test_webdav_client_requires_complete_settings(url, user, pw):
    with pytest.raises(NextcloudSyncError):
        WebDavClient(url, user, pw, timeout_seconds=5)
