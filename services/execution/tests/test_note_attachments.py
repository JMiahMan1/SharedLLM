"""Note attachments, kept as Nextcloud Notes keeps them: files in
``.attachments.<fileid>/`` beside the note, linked relatively (docs/NOTES.md)."""
import base64

import pytest

from services.execution.handlers import note as note_handler

FILEID_REPLY = (
    '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
    "<d:response><d:propstat><d:prop><oc:fileid>4242</oc:fileid></d:prop></d:propstat></d:response>"
    "</d:multistatus>"
)


class FakeProvider:
    username = "u"
    password = "p"
    base_url = "http://nextcloud.test"

    def __init__(self):
        self.dirs: list[str] = []

    async def ensure_directory(self, path):
        self.dirs.append(path)

    def file_url(self, path):
        return f"http://nextcloud.test/{path}"

    def sanitize_filename(self, value, fallback):
        return (value or fallback).replace(" ", "_")


@pytest.fixture
def provider(monkeypatch):
    p = FakeProvider()
    monkeypatch.setattr(note_handler, "resolve_personal_data_provider", lambda _ctx: p)
    return p


@pytest.fixture
def dav(monkeypatch):
    """A fake Nextcloud: ``files`` maps a URL to its body; every call is kept."""
    import services.execution.http_client as http_client

    state = {"files": {}, "calls": []}

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        state["calls"].append((method, url, headers or {}, data))
        files = state["files"]
        if method == "PROPFIND":
            if (headers or {}).get("Depth") == "0" and url in files:
                return {"status_code": 207, "text": FILEID_REPLY}
            return {"status_code": 404, "text": ""}
        if method == "GET":
            if url in files:
                return {"status_code": 200, "text": files[url], "headers": {}}
            return {"status_code": 404, "text": ""}
        if method == "PUT":
            files[url] = data
            return {"status_code": 201, "text": "", "headers": {}}
        if method in ("DELETE", "MOVE", "COPY"):
            return {"status_code": 204, "text": ""}
        return {"status_code": 404, "text": ""}

    async def fake_fetch_bytes(url, **kwargs):
        state["calls"].append(("GET-BYTES", url, {}, None))
        if url in state["files"]:
            return 200, "image/png", state["files"][url]
        return 404, "text/plain", b""

    monkeypatch.setattr(http_client, "request", fake_request)
    monkeypatch.setattr(http_client, "fetch_bytes", fake_fetch_bytes)
    return state


def request(action, **kwargs):
    from services.execution.schemas import NoteRequest

    return NoteRequest(user_context={"user": "jeremiah"}, action=action, storage="nextcloud", **kwargs)


URL = "http://nextcloud.test/"


async def test_upload_stores_the_file_beside_the_note_under_its_file_id(provider, dav):
    dav["files"][URL + "Notes/Work/Plan.md"] = "# Plan"
    png = b"\x89PNG\r\n\x1a\nfake"
    result = await note_handler.handle_note(request(
        "attachment_upload", path="Notes/Work/Plan.md", filename="My Photo (1).PNG",
        data=base64.b64encode(png).decode(),
    ))
    assert result.status == "SUCCESS", result.message
    # The link the markdown gets is relative to the note, as Nextcloud Notes writes it.
    assert result.detail == {"link": ".attachments.4242/My-Photo-1.png", "name": "My-Photo-1.png", "mime": "image/png", "size": len(png)}
    assert provider.dirs == ["Notes/Work/.attachments.4242"]
    assert dav["files"][URL + "Notes/Work/.attachments.4242/My-Photo-1.png"] == png


async def test_upload_never_overwrites_an_attachment_with_the_same_name(provider, dav):
    dav["files"][URL + "Notes/Plan.md"] = "# Plan"
    dav["files"][URL + "Notes/.attachments.4242/voice.webm"] = b"old"
    result = await note_handler.handle_note(request(
        "attachment_upload", path="Notes/Plan.md", filename="voice.webm", data=base64.b64encode(b"new").decode(),
    ))
    assert result.detail["link"] == ".attachments.4242/voice-2.webm"
    assert dav["files"][URL + "Notes/.attachments.4242/voice.webm"] == b"old"


async def test_upload_refuses_an_unsaved_note_an_empty_file_and_one_too_large(provider, dav, monkeypatch):
    unsaved = await note_handler.handle_note(request("attachment_upload", filename="a.png", data="aGk="))
    assert unsaved.status == "FAILURE" and "saved note" in unsaved.message
    empty = await note_handler.handle_note(request("attachment_upload", path="Notes/A.md", filename="a.png", data=""))
    assert empty.status == "FAILURE"
    garbled = await note_handler.handle_note(request("attachment_upload", path="Notes/A.md", filename="a.png", data="not base64!"))
    assert garbled.status == "FAILURE"
    monkeypatch.setattr(note_handler, "ATTACHMENT_MAX_BYTES", 2)
    big = await note_handler.handle_note(request("attachment_upload", path="Notes/A.md", filename="a.png", data="aGVsbG8="))
    assert big.status == "FAILURE" and "MB" in big.message
    assert not [c for c in dav["calls"] if c[0] == "PUT"]


async def test_read_returns_the_file_base64_and_follows_only_attachment_links(provider, dav):
    dav["files"][URL + "Notes/Work/.attachments.4242/a b.png"] = b"img"
    ok = await note_handler.handle_note(request(
        "attachment_read", path="Notes/Work/Plan.md", attachment=".attachments.4242/a%20b.png",
    ))
    assert ok.status == "SUCCESS"
    assert base64.b64decode(ok.detail["data"]) == b"img" and ok.detail["mime"] == "image/png"
    for bad in ("../Secrets.md", ".attachments.4242/../../x", "/etc/passwd", "http://evil/x.png", ".attachments.x/a.png", "Photos/a.png"):
        refused = await note_handler.handle_note(request("attachment_read", path="Notes/Work/Plan.md", attachment=bad))
        assert refused.status == "FAILURE", bad
    assert [c[1] for c in dav["calls"] if c[0] == "GET-BYTES"] == [URL + "Notes/Work/.attachments.4242/a b.png"]


async def test_a_missing_attachment_says_so(provider, dav):
    result = await note_handler.handle_note(request("attachment_read", path="Notes/A.md", attachment=".attachments.1/gone.png"))
    assert result.status == "FAILURE" and "not there" in result.message


async def test_delete_attachment_removes_that_file_only(provider, dav):
    result = await note_handler.handle_note(request("attachment_delete", path="Notes/A.md", attachment=".attachments.9/a.png"))
    assert result.status == "SUCCESS"
    assert [(c[0], c[1]) for c in dav["calls"]] == [("DELETE", URL + "Notes/.attachments.9/a.png")]


async def test_deleting_a_note_for_good_deletes_its_attachment_folders(provider, dav):
    dav["files"][URL + "Notes/Trash/Plan.md"] = "# Plan\n\n![](.attachments.9/a.png)\n[voice.webm](.attachments.9/voice.webm)"
    result = await note_handler.handle_note(request("delete", path="Notes/Trash/Plan.md"))
    assert result.status == "SUCCESS"
    deletes = [c[1] for c in dav["calls"] if c[0] == "DELETE"]
    assert deletes == [URL + "Notes/Trash/Plan.md", URL + "Notes/Trash/.attachments.9"]


async def test_a_renames_old_copy_hands_its_attachments_to_the_new_one(provider, dav):
    dav["files"][URL + "Notes/Plan.md"] = "# Plan\n\n![](.attachments.9/a.png)"
    result = await note_handler.handle_note(request("delete", path="Notes/Plan.md", attachments_to="Notes/Work/Plan.md"))
    assert result.status == "SUCCESS"
    assert [c[1] for c in dav["calls"] if c[0] == "DELETE"] == [URL + "Notes/Plan.md"]
    moves = [(c[1], c[2]["Destination"]) for c in dav["calls"] if c[0] == "MOVE"]
    assert moves == [(URL + "Notes/.attachments.9", URL + "Notes/Work/.attachments.9")]


async def test_a_rename_in_the_same_folder_leaves_the_attachments_alone(provider, dav):
    dav["files"][URL + "Notes/Plan.md"] = "# Plan\n\n![](.attachments.9/a.png)"
    await note_handler.handle_note(request("delete", path="Notes/Plan.md", attachments_to="Notes/Plan 2.md"))
    assert not [c for c in dav["calls"] if c[0] == "MOVE"]
    assert [c[1] for c in dav["calls"] if c[0] == "DELETE"] == [URL + "Notes/Plan.md"]


async def test_archiving_moves_the_attachment_folder_with_the_note(provider, dav):
    dav["files"][URL + "Notes/Plan.md"] = "# Plan\n\n![](.attachments.9/a.png)"
    result = await note_handler.handle_note(request("move", path="Notes/Plan.md", destination="Notes/Archive/Plan.md"))
    assert result.status == "SUCCESS"
    moves = [(c[1], c[2]["Destination"]) for c in dav["calls"] if c[0] == "MOVE"]
    assert moves == [
        (URL + "Notes/Plan.md", URL + "Notes/Archive/Plan.md"),
        (URL + "Notes/.attachments.9", URL + "Notes/Archive/.attachments.9"),
    ]


async def test_a_duplicate_gets_its_own_copy_of_the_attachments(provider, dav):
    dav["files"][URL + "Notes/Plan.md"] = "# Plan\nCategory: Notes\n\n![](.attachments.9/a.png)"
    result = await note_handler.handle_note(request("duplicate", path="Notes/Plan.md"))
    assert result.status == "SUCCESS"
    copies = [(c[1], c[2]["Destination"]) for c in dav["calls"] if c[0] == "COPY"]
    assert copies == [(URL + "Notes/.attachments.9", URL + "Notes/.attachments.4242")]
    assert "![](.attachments.4242/a.png)" in dav["files"][URL + "Notes/Plan (copy).md"].decode()


def test_attachment_folders_reads_relative_links_only():
    text = "![](.attachments.1/a.png) [x](.attachments.2/b.pdf) ![](https://x/.attachments.3/c.png) ![](.attachments.1/d.png)"
    assert note_handler.attachment_folders(text) == [".attachments.1", ".attachments.2"]


def test_files_inside_an_attachment_folder_are_never_listed_as_notes():
    listing = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/dav/Notes/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
  <d:response><d:href>/dav/Notes/a.md</d:href><d:propstat><d:prop><d:resourcetype/></d:prop></d:propstat></d:response>
  <d:response><d:href>/dav/Notes/.attachments.9/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
  <d:response><d:href>/dav/Notes/.attachments.9/readme.md</d:href><d:propstat><d:prop><d:resourcetype/></d:prop></d:propstat></d:response>
</d:multistatus>"""
    notes, folders = note_handler._md_entries(listing, "Notes")
    assert [n["path"] for n in notes] == ["Notes/a.md"]
    assert folders == ["Notes/.attachments.9"]
