import base64
import json
from pathlib import Path

import aiohttp
import pytest
from sqlmodel import Session, select

from services.bible import importer, library, main as bible_main
from services.bible.models import BibleVersion, ImportRun
from services.bible.tests.conftest import SECRET

ROOT = "/Books/Text"


class _Response:
    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self._payload = payload
        self._text = text

    async def json(self, **kwargs):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    async def text(self):
        if self._payload is not None:
            return json.dumps(self._payload)
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, response=None, boom=None):
        self.response = response
        self.boom = boom
        self.calls = []

    def post(self, url, **kwargs):
        if isinstance(self.boom, Exception):
            raise self.boom
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.boom, BaseException):
            raise self.boom
        return self.response

    def get(self, url, **kwargs):
        return self.post(url, **kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def patch_storage(monkeypatch, response=None, boom=None):
    session = _Session(response=response, boom=boom)
    monkeypatch.setattr(library.aiohttp, "ClientSession", lambda **kwargs: session)
    return session


def entry(path, is_dir=False, size=10):
    return {
        "path": path,
        "name": Path(path).name,
        "is_dir": is_dir,
        "size": size,
        "mtime": 0,
        "content_type": "application/x-pdf+xml" if path.endswith(".pdf") else "",
        "metadata": {},
    }


def listing(*entries):
    return {"status": "SUCCESS", "entries": list(entries)}


def client(root=ROOT):
    return library.LibraryClient(storage_url="http://storage:8005", internal_secret=SECRET)


def test_a_path_is_rooted_and_resolved():
    assert library.normalise_path("Books/Text/../Text//A/./B.epub") == "/Books/Text/A/B.epub"
    assert library.normalise_path("") == "/"
    assert library.normalise_path("Books\\Text") == "/Books/Text"


def test_folders_are_listed_before_files_and_unreadable_files_are_still_shown():
    raw = [entry(f"{ROOT}/Zebra.epub"), entry(f"{ROOT}/Study.docx"), entry(f"{ROOT}/Alpha.epub"), entry(f"{ROOT}/Audio", True)]
    described = library.describe(raw, path=ROOT, root=ROOT)
    assert [item.name for item in described.entries] == ["Audio", "Alpha.epub", "Study.docx", "Zebra.epub"]
    docx = described.entries[2]
    assert docx.installable is False
    assert "Not a Bible format" in docx.note
    tally = described.as_dict()
    assert tally["installable"] == 2
    assert tally["count"] == 4


def test_the_parent_points_up_except_at_the_root_where_it_points_at_itself():
    deeper = library.describe(listing(entry(f"{ROOT}/A/B.epub"))["entries"], path=f"{ROOT}/Thomas Nelson", root=ROOT)
    assert deeper.parent == ROOT
    at_root = library.describe(listing(entry(f"{ROOT}/Thomas Nelson", True))["entries"], path=ROOT, root=ROOT)
    assert at_root.parent == ""


def test_two_shelves_holding_the_same_filename_do_not_collide():
    first = library.candidate_names(f"{ROOT}/A/Bible.epub")
    second = library.candidate_names(f"{ROOT}/B/Bible.epub")
    assert first != second
    assert first.endswith("Bible.epub")


async def test_browsing_asks_storage_for_a_nextcloud_listing(monkeypatch):
    session = patch_storage(monkeypatch, _Response(payload=listing(entry(f"{ROOT}/Bible.epub"))))
    result = await client().browse(root=ROOT, path="")
    assert result.as_dict()["count"] == 1
    call = session.calls[0]
    assert call["url"] == "http://storage:8005/providers/list"
    assert call["headers"] == {"X-Internal-Secret": SECRET}
    assert call["json"] == {"provider": {"kind": "nextcloud", "settings": {}}, "path": "/Books/Text", "recursive": False}


async def test_a_blank_root_names_the_setting_that_has_to_be_set(monkeypatch):
    session = patch_storage(monkeypatch, _Response(payload=listing()))
    for root in ("", "/"):
        with pytest.raises(library.LibraryUnavailable) as caught:
            await client().browse(root=root, path="")
        assert library.LIBRARY_SETTING in str(caught.value)
    assert session.calls == []


async def test_asking_for_a_folder_outside_the_library_is_refused(monkeypatch):
    patch_storage(monkeypatch, _Response(payload=listing()))
    with pytest.raises(library.LibraryError):
        await client().browse(root=ROOT, path="/Documents")


async def test_storage_being_unreachable_is_the_operators_problem(monkeypatch):
    patch_storage(monkeypatch, boom=aiohttp.ClientError("connection refused"))
    with pytest.raises(library.LibraryUnavailable) as caught:
        await client().browse(root=ROOT, path="")
    assert "connection refused" in str(caught.value)


async def test_a_listing_that_is_not_a_list_says_what_arrived(monkeypatch):
    patch_storage(monkeypatch, _Response(payload={"status": "SUCCESS", "entries": {"a": 1}}))
    with pytest.raises(library.LibraryUnavailable) as caught:
        await client().browse(root=ROOT, path="")
    assert "entries" in str(caught.value)


async def test_a_listing_that_is_not_json_is_refused(monkeypatch):
    patch_storage(monkeypatch, _Response(text="<html>nope</html>"))
    with pytest.raises(library.LibraryUnavailable):
        await client().browse(root=ROOT, path="")


def test_missing_configuration_names_both_settings():
    with pytest.raises(library.LibraryUnavailable) as caught:
        library.LibraryClient(storage_url="", internal_secret=SECRET)._endpoint()
    assert "storage_svc_url" in str(caught.value)
    with pytest.raises(library.LibraryUnavailable) as caught:
        library.LibraryClient(storage_url="http://storage:8005", internal_secret="")._endpoint()
    assert "INTERNAL_SECRET" in str(caught.value)


async def test_a_fetch_writes_the_exact_bytes_and_leaves_no_part_file(monkeypatch, tmp_path):
    body = b"%PDF-1.4 fake"
    encoded = base64.b64encode(body).decode()
    session = patch_storage(monkeypatch, _Response(payload={"status": "SUCCESS", "path": "x.pdf", "name": "x.pdf", "size": len(body), "content_b64": encoded}))
    destination = tmp_path / "x.pdf"
    written = await client().fetch(root=ROOT, path=f"{ROOT}/x.pdf", destination=destination)
    assert written.read_bytes() == body
    assert list(tmp_path.glob("*.part")) == []
    assert session.calls[0]["url"] == "http://storage:8005/providers/fetch"


async def test_an_empty_body_is_refused_and_nothing_is_written(monkeypatch, tmp_path):
    patch_storage(monkeypatch, _Response(payload={"status": "SUCCESS", "path": "x.pdf", "content_b64": ""}))
    destination = tmp_path / "x.pdf"
    with pytest.raises(library.LibraryUnavailable) as caught:
        await client().fetch(root=ROOT, path=f"{ROOT}/x.pdf", destination=destination)
    assert "nothing was written" in str(caught.value)
    assert not destination.exists()


async def test_a_body_that_is_not_base64_is_refused(monkeypatch, tmp_path):
    patch_storage(monkeypatch, _Response(payload={"status": "SUCCESS", "path": "x.pdf", "content_b64": "not base64 !!"}))
    with pytest.raises(library.LibraryUnavailable) as caught:
        await client().fetch(root=ROOT, path=f"{ROOT}/x.pdf", destination=tmp_path / "x.pdf")
    assert "base64" in str(caught.value)


async def test_an_unreadable_format_is_refused_without_asking_storage(monkeypatch, tmp_path):
    session = patch_storage(monkeypatch, _Response(payload=listing()))
    with pytest.raises(library.LibraryError):
        await client().fetch(root=ROOT, path=f"{ROOT}/Bible.docx", destination=tmp_path / "Bible.docx")
    assert session.calls == []


def test_a_library_import_is_labelled_with_its_shelf_path():
    plan = importer.ImportPlan(code="kjv", kind="epub", name="KJV", library_path=f"{ROOT}/KJV.epub")
    assert plan.source_label() == f"nextcloud:{ROOT}/KJV.epub"
    assert plan.source_path == ""


def test_a_fetched_plan_does_not_go_back_to_the_shelf():
    plan = importer.ImportPlan(code="kjv", kind="epub", name="KJV", library_path=f"{ROOT}/KJV.epub")
    fetched = plan._replace_source("/data/imports/nextcloud-KJV.epub")
    assert fetched.library_path == ""
    assert fetched.provider == ""
    assert fetched.source_path == "/data/imports/nextcloud-KJV.epub"


def test_the_run_history_records_the_shelf_and_not_the_local_copy(session):
    plan = importer.ImportPlan(code="kjv", kind="epub", name="KJV", library_path=f"{ROOT}/KJV.epub")
    importer._record(session, plan, importer.ImportReport(status="failed", kind="epub", code="kjv", source=plan.source_label(), message="no"), 5)
    run = session.exec(select(ImportRun)).one()
    assert run.source == f"nextcloud:{ROOT}/KJV.epub"
    assert run.status == "failed"


def test_the_browse_route_returns_the_shelf(loaded_client, monkeypatch):
    async def browse(self, **kwargs):
        return library.describe(listing(entry(f"{ROOT}/Bible.epub"))["entries"], path=ROOT, root=ROOT)

    monkeypatch.setattr(library.LibraryClient, "browse", browse)
    async def root():
        return ROOT

    monkeypatch.setattr(bible_main, "_library_root", root)
    response = loaded_client.get("/admin/library", headers={"X-Internal-Secret": SECRET})
    assert response.status_code == 200
    body = response.json()
    assert body["root"] == ROOT
    assert body["setting"] == library.LIBRARY_SETTING
    assert body["count"] == 1
    assert body["entries"][0]["installable"] is True


def test_the_browse_route_says_which_folder_has_to_be_set(loaded_client, monkeypatch):
    async def root():
        return ""

    monkeypatch.setattr(bible_main, "_library_root", root)
    response = loaded_client.get("/admin/library", headers={"X-Internal-Secret": SECRET})
    assert response.status_code == 503
    assert "calibre_library_path" in response.json()["detail"]


def test_a_browse_error_from_the_shelf_is_a_bad_request(loaded_client, monkeypatch):
    async def browse(self, **kwargs):
        raise library.LibraryError("outside the library folder")

    async def root():
        return ROOT

    monkeypatch.setattr(library.LibraryClient, "browse", browse)
    monkeypatch.setattr(bible_main, "_library_root", root)
    response = loaded_client.get("/admin/library", headers={"X-Internal-Secret": SECRET})
    assert response.status_code == 400


async def test_the_catalogue_reports_where_the_shelf_is(loaded_client, monkeypatch):
    async def root():
        return ROOT

    monkeypatch.setattr(bible_main, "_library_root", root)
    response = loaded_client.get("/admin/imports", headers={"X-Internal-Secret": SECRET})
    body = response.json()
    assert body["library_root"] == ROOT
    assert body["library_setting"] == library.LIBRARY_SETTING
    assert body["library_error"] == ""


async def test_an_unset_shelf_is_reported_next_to_the_settings_it_needs(loaded_client, monkeypatch):
    async def root():
        return ""

    monkeypatch.setattr(bible_main, "_library_root", root)
    response = loaded_client.get("/admin/imports", headers={"X-Internal-Secret": SECRET})
    body = response.json()
    assert body["library_root"] == ""
    assert "calibre_library_path" in body["library_error"]
    assert "Books/Text" in body["library_error"]


async def test_a_shelf_epub_is_fetched_then_imported_like_an_upload(loaded_client, tmp_path, monkeypatch):
    async def root():
        return ROOT

    async def fetch(self, *, root, path, destination):
        payload = b"PK\x03\x04 not really a bible"
        destination.write_bytes(payload)
        return destination

    monkeypatch.setattr(bible_main, "_library_root", root)
    monkeypatch.setattr(library.LibraryClient, "fetch", fetch)
    monkeypatch.setattr(bible_main, "_import_dir", lambda: Path(str(tmp_path)))
    response = loaded_client.post(
        "/admin/imports",
        headers={"X-Internal-Secret": SECRET},
        json={"code": "kjv", "kind": "epub", "name": "Some Bible", "library_path": f"{ROOT}/Bible.epub"},
    )
    assert response.status_code == 422
    report = response.json()
    assert report["status"] == "failed"
    assert report["source"] == f"nextcloud:{ROOT}/Bible.epub"
    assert report["log"][0].startswith("Fetched")
    assert "nextcloud-" in report["log"][0] and report["log"][0].endswith("Bible.epub (23 bytes).")
    fetched = sorted(item.name for item in tmp_path.iterdir() if item.name.startswith("nextcloud-"))
    assert fetched == ["nextcloud-b618602e-Bible.epub"]
    with Session(loaded_client.app.state.engine) as check:
        assert check.exec(select(BibleVersion).where(BibleVersion.code == "kjv-shelf-import")).first() is None


async def test_a_library_import_without_an_import_folder_is_refused(loaded_client, monkeypatch):
    monkeypatch.setattr(bible_main, "_import_dir", lambda: None)
    response = loaded_client.post(
        "/admin/imports",
        headers={"X-Internal-Secret": SECRET},
        json={"code": "kjv", "kind": "epub", "name": "Some Bible", "library_path": f"{ROOT}/Bible.epub"},
    )
    assert response.status_code == 422
    assert "BIBLE_IMPORT_DIR" in response.json()["message"]


def test_having_nothing_at_all_names_the_shelf_as_an_option(loaded_client):
    response = loaded_client.post(
        "/admin/imports",
        headers={"X-Internal-Secret": SECRET},
        json={"code": "kjv", "kind": "epub", "name": "Some Bible"},
    )
    assert response.status_code == 422
    assert "library_path" in response.json()["message"]
