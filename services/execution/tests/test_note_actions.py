

import pytest

from services.execution.handlers import note as note_handler


class FakeProvider:
    username = "u"
    password = "p"

    def __init__(self):
        self.dirs: list[str] = []
        self.writes: list[tuple[str, str]] = []

    async def ensure_directory(self, path):
        self.dirs.append(path)

    def file_url(self, path):
        return f"http://nextcloud.test/{path}"

    def sanitize_filename(self, value, fallback):
        return (value or fallback).replace(" ", "_")


class FakeResponse(dict):
    pass


@pytest.fixture
def provider(monkeypatch):
    p = FakeProvider()
    monkeypatch.setattr(note_handler, "resolve_personal_data_provider", lambda _ctx: p)
    return p


def request(action, **kwargs):
    payload = {
        "user_context": {"user": "jeremiah"},
        "action": action,
        "storage": "nextcloud",
        **kwargs,
    }
    from services.execution.schemas import NoteRequest

    return NoteRequest(**payload)


async def test_write_replaces_content_instead_of_appending(provider, monkeypatch):
    import services.execution.http_client as http_client

    calls: list[tuple[str, str, bytes | None]] = []

    async def fake_request(method, url, data=None, **kwargs):
        calls.append((method, url, data))
        return {"status_code": 204, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(request("write", title="Groceries", content="milk\neggs"))

    assert result.status == "SUCCESS"
    assert result.service == "note_write"
    methods = [c[0] for c in calls]
    # A save is a single PUT. If this ever becomes GET+PUT, it is append again.
    assert methods == ["PUT"]
    body = calls[0][2].decode()
    assert "milk\neggs" in body
    assert "- [ ]" not in body


async def test_check_off_toggles_an_item(provider, monkeypatch):
    import services.execution.http_client as http_client

    stored = {"text": "# List\nCategory: Notes\n\n- [ ] milk\n- [ ] eggs"}
    puts: list[bytes] = []

    async def fake_request(method, url, data=None, **kwargs):
        if method == "GET":
            return {"status_code": 200, "text": stored["text"]}
        puts.append(data or b"")
        return {"status_code": 204, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(request("check_off", title="List", item="milk"))

    assert result.status == "SUCCESS"
    assert result.service == "note_check_off"
    assert len(puts) == 1
    assert "- [x] milk" in puts[0].decode()
    assert "- [ ] eggs" in puts[0].decode()


async def test_check_off_reports_missing_item(provider, monkeypatch):
    import services.execution.http_client as http_client

    async def fake_request(method, url, data=None, **kwargs):
        return {"status_code": 200, "text": "# List\n\n- [ ] milk"}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(request("check_off", title="List", item="bread"))

    assert result.status == "FAILURE"
    assert "not found" in result.message.lower()


async def test_check_off_requires_item(provider):
    result = await note_handler.handle_note(request("check_off", title="List"))
    assert result.status == "FAILURE"


async def test_create_reports_where_the_note_was_written(provider, monkeypatch):
    """A caller that wants to point at the note later needs the real path.

    The filename is this handler's rule, so the path is handed back rather than
    rebuilt by the caller -- a second implementation of "sanitize_filename"
    would drift and start pointing at files that do not exist.
    """
    import services.execution.http_client as http_client

    async def fake_request(method, url, data=None, **kwargs):
        return {"status_code": 201, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(
        request("create", title="John 3:16", category="Bible", content="text")
    )

    assert result.status == "SUCCESS"
    assert result.detail is not None
    assert result.detail["path"] == "Bible/John_3:16.md"


async def test_write_reports_where_the_note_was_written(provider, monkeypatch):
    import services.execution.http_client as http_client

    async def fake_request(method, url, data=None, **kwargs):
        return {"status_code": 204, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(
        request("write", title="John 3:16", category="Bible", content="text")
    )
    assert result.detail is not None
    assert result.detail["path"] == "Bible/John_3:16.md"


async def test_write_to_an_explicit_path_reports_that_path(provider, monkeypatch):
    import services.execution.http_client as http_client

    async def fake_request(method, url, data=None, **kwargs):
        return {"status_code": 204, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(
        request("write", title="John 3:16", path="/Bible/John 3_16.md", content="text")
    )
    assert result.detail is not None
    assert result.detail["path"] == "Bible/John 3_16.md"


async def test_saving_what_read_returned_does_not_stack_headers(provider, monkeypatch):
    """read hands back the whole file; saving it must not add a second header."""
    import services.execution.http_client as http_client

    puts: list[str] = []

    async def fake_request(method, url, data=None, **kwargs):
        puts.append((data or b"").decode())
        return {"status_code": 204, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)

    # Including a note already corrupted by earlier saves: it heals.
    stacked = "# Groceries\nCategory: Notes\n\n# Groceries\nCategory: Notes\n\nmilk\n# Heading kept"
    await note_handler.handle_note(request("write", title="Groceries", category="Notes", content=stacked))

    assert puts[0] == "# Groceries\nCategory: Notes\n\nmilk\n# Heading kept"


def test_note_body_keeps_a_markdown_heading_that_is_not_the_stored_header():
    assert note_handler.note_body("# Shopping\n- [ ] milk") == "# Shopping\n- [ ] milk"
    assert note_handler.note_body("# T\nCategory: Recipes\n\nStep 1") == "Step 1"


# --- list with previews: one request instead of one read per card ----------

_PROPFIND = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/dav/Notes/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
  {items}
</d:multistatus>"""
_ITEM = (
    "<d:response><d:href>/dav/Notes/{name}.md</d:href><d:propstat><d:prop><d:resourcetype/>"
    "<d:getlastmodified>{modified}</d:getlastmodified><d:getcontentlength>{size}</d:getcontentlength>"
    "</d:prop></d:propstat></d:response>"
)


def _listing(*notes):
    return _PROPFIND.format(items="".join(_ITEM.format(name=n, modified=m, size=s) for n, m, s in notes))


@pytest.fixture
def fresh_cache():
    note_handler._preview_cache.clear()
    yield
    note_handler._preview_cache.clear()


async def test_list_with_previews_reads_each_note_once_and_reuses_it(provider, monkeypatch, fresh_cache):
    import services.execution.http_client as http_client

    listing = _listing(
        ("Older", "Mon, 13 Oct 2026 09:00:00 GMT", 10),
        ("Newer", "Wed, 15 Oct 2026 09:00:00 GMT", 12),
    )
    gets: list[str] = []
    propfinds: list[dict] = []

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        if method == "PROPFIND":
            propfinds.append({"headers": headers, "data": data})
            return {"status_code": 207, "text": listing}
        gets.append(url)
        return {"status_code": 200, "text": f"# t\nCategory: Notes\n\nbody of {url.rsplit('/', 1)[-1]}"}

    monkeypatch.setattr(http_client, "request", fake_request)

    first = await note_handler.handle_note(request("list", previews=True))

    assert first.status == "SUCCESS"
    notes = first.detail["notes"]
    # Newest first by date: "Wed" sorts before "Mon" as text, which put the
    # oldest notes first and previewed the wrong ones.
    assert [n["title"] for n in notes] == ["Newer", "Older"]
    assert notes[0]["content"].endswith("body of Newer.md")
    assert len(gets) == 2
    # Only the properties the list shows are asked for.
    assert "getlastmodified" in propfinds[0]["data"] and "quota" not in propfinds[0]["data"]

    second = await note_handler.handle_note(request("list", previews=True))

    assert [n["content"] for n in second.detail["notes"]] == [n["content"] for n in notes]
    assert len(gets) == 2, "unchanged notes are not read again"


async def test_a_save_forgets_the_cached_text_of_that_note(provider, monkeypatch, fresh_cache):
    import services.execution.http_client as http_client

    listing = _listing(("List", "Wed, 15 Oct 2026 09:00:00 GMT", 20))
    body = {"text": "# List\nCategory: Notes\n\n- [ ] milk"}

    async def fake_request(method, url, data=None, **kwargs):
        if method == "PROPFIND":
            return {"status_code": 207, "text": listing}
        if method == "PUT":
            body["text"] = data.decode()
            return {"status_code": 204, "text": ""}
        return {"status_code": 200, "text": body["text"]}

    monkeypatch.setattr(http_client, "request", fake_request)

    await note_handler.handle_note(request("list", previews=True))
    # Same size and second as the listing: only the explicit forget catches it.
    await note_handler.handle_note(request("check_off", title="List", path="Notes/List.md", item="milk"))
    after = await note_handler.handle_note(request("list", previews=True))

    assert "- [x] milk" in after.detail["notes"][0]["content"]


async def test_previews_are_read_a_few_at_a_time(provider, monkeypatch, fresh_cache):
    import asyncio

    import services.execution.http_client as http_client

    listing = _listing(*[(f"n{i}", "Wed, 15 Oct 2026 09:00:00 GMT", i) for i in range(30)])
    active = {"now": 0, "peak": 0}

    async def fake_request(method, url, **kwargs):
        if method == "PROPFIND":
            return {"status_code": 207, "text": listing}
        active["now"] += 1
        active["peak"] = max(active["peak"], active["now"])
        await asyncio.sleep(0)
        active["now"] -= 1
        return {"status_code": 200, "text": "x"}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(request("list", previews=True))

    with_text = [n for n in result.detail["notes"] if "content" in n]
    assert len(with_text) == note_handler.PREVIEW_LIMIT
    assert active["peak"] <= note_handler.PREVIEW_CONCURRENCY


async def test_an_unreadable_preview_does_not_fail_the_list(provider, monkeypatch, fresh_cache):
    import services.execution.http_client as http_client

    listing = _listing(("A", "Wed, 15 Oct 2026 09:00:00 GMT", 1), ("B", "Tue, 14 Oct 2026 09:00:00 GMT", 1))

    async def fake_request(method, url, **kwargs):
        if method == "PROPFIND":
            return {"status_code": 207, "text": listing}
        if url.endswith("A.md"):
            raise TimeoutError()
        return {"status_code": 200, "text": "b"}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(request("list", previews=True))

    assert result.status == "SUCCESS"
    a, b = result.detail["notes"]
    assert "content" not in a and b["content"] == "b"


async def test_a_plain_list_reads_no_note_bodies(provider, monkeypatch, fresh_cache):
    import services.execution.http_client as http_client

    methods: list[str] = []

    async def fake_request(method, url, **kwargs):
        methods.append(method)
        return {"status_code": 207, "text": _listing(("A", "Wed, 15 Oct 2026 09:00:00 GMT", 1))}

    monkeypatch.setattr(http_client, "request", fake_request)

    await note_handler.handle_note(request("list"))

    assert methods == ["PROPFIND"]


async def test_a_timeout_is_named_in_the_error(provider, monkeypatch):
    import services.execution.http_client as http_client

    async def fake_request(method, url, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(request("read", title="x", path="Notes/x.md"))

    assert result.status == "FAILURE"
    assert result.message == "Note error: TimeoutError"


async def test_the_folder_check_asks_for_the_folder_alone(monkeypatch):
    from services.execution import nextcloud_client

    seen = {}

    async def fake_request(method, url, headers=None, data=None, **kwargs):
        seen.update(method=method, headers=headers, data=data)
        return {"status_code": 207, "text": ""}

    monkeypatch.setattr(nextcloud_client, "request", fake_request)

    await nextcloud_client.ensure_webdav_dir("https://cloud.test", "u", "p", "Notes")

    # No Depth header means "list everything in it" to Nextcloud.
    assert seen["method"] == "PROPFIND"
    assert seen["headers"]["Depth"] == "0"


# --- listing the tree: one request, not one per folder ----------------------

_TREE = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/dav/Notes/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
  <d:response><d:href>/dav/Notes/Small%20Group/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
  <d:response><d:href>/dav/Notes/top.md</d:href><d:propstat><d:prop><d:resourcetype/><d:getlastmodified>Tue, 14 Oct 2026 09:00:00 GMT</d:getlastmodified><d:getcontentlength>5</d:getcontentlength></d:prop></d:propstat></d:response>
  <d:response><d:href>/dav/Notes/Small%20Group/week%201.md</d:href><d:propstat><d:prop><d:resourcetype/><d:getlastmodified>Wed, 15 Oct 2026 09:00:00 GMT</d:getlastmodified><d:getcontentlength>7</d:getcontentlength></d:prop></d:propstat></d:response>
  <d:response><d:href>/dav/Notes/Small%20Group/photo.jpg</d:href><d:propstat><d:prop><d:resourcetype/></d:prop></d:propstat></d:response>
</d:multistatus>"""


async def test_list_reads_the_whole_tree_in_one_request_and_never_creates_the_folder(provider, monkeypatch):
    import services.execution.http_client as http_client

    calls: list[tuple[str, str]] = []

    async def fake_request(method, url, headers=None, **kwargs):
        calls.append((method, (headers or {}).get("Depth", "")))
        return {"status_code": 207, "text": _TREE}

    monkeypatch.setattr(http_client, "request", fake_request)
    result = await note_handler.handle_note(request("list"))

    assert result.status == "SUCCESS"
    assert calls == [("PROPFIND", "infinity")]
    assert provider.dirs == []
    notes = result.detail["notes"]
    assert [(n["title"], n["path"]) for n in notes] == [
        ("week 1", "Notes/Small Group/week 1.md"),
        ("top", "Notes/top.md"),
    ]


async def test_list_falls_back_to_listing_folders_when_infinite_depth_is_refused(provider, monkeypatch):
    import services.execution.http_client as http_client

    top = _TREE.replace(
        '<d:response><d:href>/dav/Notes/Small%20Group/week%201.md</d:href>', '<d:response><d:href>/x/</d:href>'
    )
    sub = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/dav/Notes/Small%20Group/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
  <d:response><d:href>/dav/Notes/Small%20Group/week%201.md</d:href><d:propstat><d:prop><d:resourcetype/><d:getlastmodified>Wed, 15 Oct 2026 09:00:00 GMT</d:getlastmodified><d:getcontentlength>7</d:getcontentlength></d:prop></d:propstat></d:response>
</d:multistatus>"""
    seen: list[tuple[str, str]] = []

    async def fake_request(method, url, headers=None, **kwargs):
        depth = (headers or {}).get("Depth", "")
        seen.append((depth, url))
        if depth == "infinity":
            return {"status_code": 403, "text": ""}
        if url.endswith("Small Group"):
            return {"status_code": 207, "text": sub}
        return {"status_code": 207, "text": top}

    monkeypatch.setattr(http_client, "request", fake_request)
    result = await note_handler.handle_note(request("list"))

    assert sorted(n["path"] for n in result.detail["notes"]) == ["Notes/Small Group/week 1.md", "Notes/top.md"]
    assert [d for d, _ in seen] == ["infinity", "1", "1"]


async def test_list_of_a_missing_folder_is_empty(provider, monkeypatch):
    import services.execution.http_client as http_client

    async def fake_request(method, url, headers=None, **kwargs):
        return {"status_code": 404, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)
    result = await note_handler.handle_note(request("list"))

    assert result.status == "SUCCESS"
    assert result.detail["notes"] == []
    assert provider.dirs == []


async def test_local_append_adds_a_checklist_item(monkeypatch, tmp_path):
    from services.execution.schemas import NoteRequest

    monkeypatch.setattr(note_handler, "LOCAL_NOTES_ROOT", str(tmp_path))
    (tmp_path / "Groceries.md").write_text("# Groceries\n", encoding="utf-8")
    result = await note_handler.handle_note(
        NoteRequest(user_context={"user": "jeremiah"}, action="append", storage="local", title="Groceries", content="milk")
    )

    assert result.status == "SUCCESS", result.message
    assert (tmp_path / "Groceries.md").read_text(encoding="utf-8").endswith("- [ ] milk")


# --- Keep-style index, move, duplicate and trash ---------------------------


async def test_meta_read_defaults_when_index_is_missing(provider, monkeypatch):
    import services.execution.http_client as http_client

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        assert method == "GET"
        assert url == "http://nextcloud.test/Notes/.jarvis-notes.json"
        return {"status_code": 404, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)
    result = await note_handler.handle_note(request("meta_read"))

    assert result.status == "SUCCESS"
    assert result.detail["meta"] == {"version": 1, "notes": {}}


async def test_meta_read_refuses_a_corrupt_index(provider, monkeypatch):
    import services.execution.http_client as http_client

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        return {"status_code": 200, "text": "{not json"}

    monkeypatch.setattr(http_client, "request", fake_request)
    result = await note_handler.handle_note(request("meta_read"))

    assert result.status == "FAILURE"
    assert "index" in result.message.lower()


async def test_meta_read_retries_once_after_a_connect_stall(provider, monkeypatch):
    import asyncio

    import services.execution.http_client as http_client

    calls = {"n": 0}

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise asyncio.TimeoutError()
        return {"status_code": 200, "text": '{"version": 1, "notes": {}}'}

    monkeypatch.setattr(http_client, "request", fake_request)
    result = await note_handler.handle_note(request("meta_read"))

    assert result.status == "SUCCESS"
    assert calls["n"] == 2
    assert result.detail["meta"] == {"version": 1, "notes": {}}


async def test_meta_write_stores_compact_json_in_notes(provider, monkeypatch):
    import json as _json

    import services.execution.http_client as http_client

    puts: list[tuple[str, bytes, dict]] = []

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        puts.append((url, data, headers or {}))
        return {"status_code": 204, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)
    meta = {"version": 1, "notes": {"Notes/Groceries.md": {"pinned": True, "color": "amber"}}}
    result = await note_handler.handle_note(request("meta_write", meta=meta))

    assert result.status == "SUCCESS"
    assert result.detail["meta"] == meta
    assert provider.dirs == ["Notes"]
    url, data, headers = puts[0]
    assert url == "http://nextcloud.test/Notes/.jarvis-notes.json"
    assert _json.loads(data) == meta
    assert "application/json" in headers["Content-Type"]


async def test_meta_write_rejects_a_bad_shape(provider, monkeypatch):
    import services.execution.http_client as http_client

    puts: list[str] = []

    async def fake_request(method, url, data=None, **kwargs):
        puts.append(method)
        return {"status_code": 204, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)
    result = await note_handler.handle_note(request("meta_write", meta={"notes": []}))

    assert result.status == "FAILURE"
    assert puts == []


async def test_meta_write_rejects_an_oversized_index(provider, monkeypatch):
    import services.execution.http_client as http_client

    puts: list[str] = []

    async def fake_request(method, url, data=None, **kwargs):
        puts.append(method)
        return {"status_code": 204, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)
    huge = {"version": 1, "notes": {"a": {"blob": "x" * (note_handler.META_MAX_BYTES + 1)}}}
    result = await note_handler.handle_note(request("meta_write", meta=huge))

    assert result.status == "FAILURE"
    assert puts == []


async def test_move_sends_webdav_move_and_records_the_original_path(provider, monkeypatch):
    import json as _json

    import services.execution.http_client as http_client

    written: dict = {}

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        if method == "MOVE":
            assert url == "http://nextcloud.test/Notes/Old.md"
            assert headers["Destination"] == "http://nextcloud.test/Notes/Archive/Old.md"
            assert headers["Overwrite"] == "T"
            return {"status_code": 201, "text": ""}
        if method == "PUT":
            written.update(_json.loads(data))
            return {"status_code": 204, "text": ""}
        return {"status_code": 404, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)
    result = await note_handler.handle_note(
        request("move", path="Notes/Old.md", destination="Notes/Archive/Old.md")
    )

    assert result.status == "SUCCESS"
    assert result.detail["path"] == "Notes/Archive/Old.md"
    assert result.detail["from"] == "Notes/Old.md"
    assert provider.dirs == ["Notes/Archive", "Notes"]
    assert written["notes"]["Notes/Archive/Old.md"]["previousPath"] == "Notes/Old.md"


async def test_move_never_overwrites_an_existing_note(provider, monkeypatch):
    import json as _json

    import services.execution.http_client as http_client

    moves: list[str] = []
    written: dict = {}

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        if method == "PROPFIND":
            status = 207 if url == "http://nextcloud.test/Notes/Archive/Plan.md" else 404
            return {"status_code": status, "text": ""}
        if method == "MOVE":
            moves.append(headers["Destination"])
            return {"status_code": 201, "text": ""}
        if method == "PUT":
            written.update(_json.loads(data))
            return {"status_code": 204, "text": ""}
        return {"status_code": 404, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)
    result = await note_handler.handle_note(
        request("move", path="Notes/Work/Plan.md", destination="Notes/Archive/Plan.md")
    )

    assert result.status == "SUCCESS"
    assert result.detail["path"] == "Notes/Archive/Plan (copy).md"
    assert moves == ["http://nextcloud.test/Notes/Archive/Plan (copy).md"]
    assert written["notes"]["Notes/Archive/Plan (copy).md"]["previousPath"] == "Notes/Work/Plan.md"


async def test_move_back_out_of_archive_restores_the_entry(provider, monkeypatch):
    import json as _json

    import services.execution.http_client as http_client

    existing = {
        "version": 1,
        "notes": {"Notes/Archive/Old.md": {"previousPath": "Notes/Old.md", "pinned": True}},
    }
    written: dict = {}

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        if method == "GET":
            return {"status_code": 200, "text": _json.dumps(existing)}
        if method == "MOVE":
            return {"status_code": 204, "text": ""}
        if method == "PUT":
            written.update(_json.loads(data))
            return {"status_code": 204, "text": ""}
        return {"status_code": 404, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)
    result = await note_handler.handle_note(
        request("move", path="Notes/Archive/Old.md", destination="Notes/Old.md")
    )

    assert result.status == "SUCCESS"
    assert written["notes"]["Notes/Old.md"] == {"pinned": True}


async def test_move_to_the_same_path_does_nothing(provider, monkeypatch):
    import services.execution.http_client as http_client

    calls: list[str] = []

    async def fake_request(method, url, data=None, **kwargs):
        calls.append(method)
        return {"status_code": 204, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)
    result = await note_handler.handle_note(
        request("move", path="Notes/Old.md", destination="Notes/Old.md")
    )

    assert result.status == "SUCCESS"
    assert calls == []


async def test_duplicate_copies_to_a_name_that_does_not_exist(provider, monkeypatch):
    import services.execution.http_client as http_client

    source = "# Old\nCategory: Notes\n\nbody text"
    puts: list[tuple[str, bytes]] = []

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        if method == "GET":
            return {"status_code": 200, "text": source}
        if method == "PROPFIND":
            # The first candidate is taken; the copy name is free.
            return {"status_code": 207 if url.endswith("/Notes/New.md") else 404, "text": ""}
        if method == "PUT":
            puts.append((url, data))
            return {"status_code": 204, "text": ""}
        return {"status_code": 404, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)
    result = await note_handler.handle_note(request("duplicate", path="Notes/Old.md", title="New"))

    assert result.status == "SUCCESS"
    assert result.detail["path"] == "Notes/New (copy).md"
    assert result.detail["title"] == "New (copy)"
    url, data = puts[0]
    assert url == "http://nextcloud.test/Notes/New (copy).md"
    assert data.decode() == "# New (copy)\nCategory: Notes\n\nbody text"


async def test_empty_trash_deletes_every_note_and_forgets_them(provider, monkeypatch):
    import json as _json

    import services.execution.http_client as http_client

    listing = _listing(
        ("Trash/a", "Mon, 13 Oct 2026 09:00:00 GMT", 3),
        ("Trash/b", "Tue, 14 Oct 2026 09:00:00 GMT", 3),
    )
    existing = {"version": 1, "notes": {"Notes/Trash/a.md": {}, "Notes/Old.md": {}}}
    deleted: list[str] = []
    written: dict = {}

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        if method == "PROPFIND":
            return {"status_code": 207, "text": listing}
        if method == "DELETE":
            deleted.append(url)
            return {"status_code": 204, "text": ""}
        if method == "GET":
            return {"status_code": 200, "text": _json.dumps(existing)}
        if method == "PUT":
            written.update(_json.loads(data))
            return {"status_code": 204, "text": ""}
        return {"status_code": 404, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)
    result = await note_handler.handle_note(request("empty_trash"))

    assert result.status == "SUCCESS"
    assert sorted(result.detail["deleted"]) == ["Notes/Trash/a.md", "Notes/Trash/b.md"]
    assert deleted == [
        "http://nextcloud.test/Notes/Trash/a.md",
        "http://nextcloud.test/Notes/Trash/b.md",
    ]
    assert "Notes/Trash/a.md" not in written["notes"]
    assert "Notes/Old.md" in written["notes"]


async def test_new_actions_stay_unavailable_in_local_mode(monkeypatch):
    from services.execution.schemas import NoteRequest

    result = await note_handler.handle_note(
        NoteRequest(user_context={"user": "jeremiah"}, action="meta_read", storage="local")
    )

    assert result.status == "FAILURE"


def test_item_matches_raw_or_plain_text_of_a_styled_row():
    from services.execution.handlers.note import _item_matches

    styled = 'buy <span style="color:#dc2626">milk</span>'
    assert _item_matches(styled, "buy milk")
    assert _item_matches(styled, styled)
    assert _item_matches("  eggs ", "eggs")
    assert not _item_matches(styled, "buy bread")
