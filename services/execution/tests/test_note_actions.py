

import pytest

from services.execution.handlers import note as note_handler


class FakeProvider:
    username = "u"
    password = "p"
    base_url = "http://nextcloud.test"

    def __init__(self):
        self.dirs: list[str] = []
        self.writes: list[tuple[str, str]] = []
        self.ocs_calls = []

    async def ensure_directory(self, path):
        self.dirs.append(path)

    def file_url(self, path):
        return f"http://nextcloud.test/{path}"

    def sanitize_filename(self, value, fallback):
        return (value or fallback).replace(" ", "_")

    #: OCS calls (method, endpoint, params, data) and the answer a test sets.
    ocs_calls: list
    ocs_answer: tuple = (True, None, "")

    async def request(self, method, endpoint, *, params=None, data=None, timeout=30):
        self.ocs_calls.append((method, endpoint, params, data))
        return self.ocs_answer


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


async def test_read_returns_the_version_it_read(provider, monkeypatch):
    import services.execution.http_client as http_client

    async def fake_request(method, url, data=None, **kwargs):
        return {"status_code": 200, "text": "# A\nCategory: Notes\n\nx", "headers": {"Etag": '"v1"'}}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(request("read", title="A", path="Notes/A.md"))
    assert result.status == "SUCCESS"
    assert result.detail == {"etag": '"v1"'}


async def test_an_in_place_save_sends_if_match_and_returns_the_new_version(provider, monkeypatch):
    import services.execution.http_client as http_client

    sent: list[dict] = []

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        sent.append(headers or {})
        return {"status_code": 204, "text": "", "headers": {"OC-ETag": '"v2"'}}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(
        request("write", title="A", category="Notes", path="Notes/A.md", content="x", if_match='"v1"')
    )
    assert result.status == "SUCCESS"
    assert sent[0]["If-Match"] == '"v1"'
    assert result.detail == {"path": "Notes/A.md", "etag": '"v2"'}


async def test_a_save_over_a_newer_file_is_refused_as_a_conflict(provider, monkeypatch):
    import services.execution.http_client as http_client

    async def fake_request(method, url, data=None, **kwargs):
        return {"status_code": 412, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(
        request("write", title="A", category="Notes", path="Notes/A.md", content="x", if_match='"old"')
    )
    assert result.status == "FAILURE"
    assert result.detail == {"conflict": True, "path": "Notes/A.md"}


async def test_a_new_note_never_sends_if_match(provider, monkeypatch):
    """The version names the file at ``path``; a new file has none to check."""
    import services.execution.http_client as http_client

    sent: list[dict] = []

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        sent.append(headers or {})
        return {"status_code": 201, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)

    await note_handler.handle_note(request("write", title="New", category="Notes", content="x", if_match='"v1"'))
    assert "If-Match" not in sent[0]


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


_FILEID_REPLY = (
    '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
    "<d:response><d:href>/remote.php/dav/files/u/Notes/A.md</d:href><d:propstat><d:prop>"
    "<oc:fileid>4242</oc:fileid></d:prop></d:propstat></d:response></d:multistatus>"
)
_VERSIONS_REPLY = (
    '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:nc="http://nextcloud.org/ns">'
    "<d:response><d:href>/remote.php/dav/versions/u/versions/4242/</d:href><d:propstat><d:prop/></d:propstat></d:response>"
    "<d:response><d:href>/remote.php/dav/versions/u/versions/4242/1700000000</d:href><d:propstat><d:prop>"
    "<d:getlastmodified>Tue, 14 Nov 2023 22:13:20 GMT</d:getlastmodified><d:getcontentlength>12</d:getcontentlength>"
    "</d:prop></d:propstat></d:response>"
    "<d:response><d:href>/remote.php/dav/versions/u/versions/4242/1700003600</d:href><d:propstat><d:prop>"
    "<d:getlastmodified>Tue, 14 Nov 2023 23:13:20 GMT</d:getlastmodified><d:getcontentlength>30</d:getcontentlength>"
    "<nc:version-label>Before trip</nc:version-label><nc:version-author>u</nc:version-author>"
    "</d:prop></d:propstat></d:response></d:multistatus>"
)


async def test_versions_lists_earlier_copies_newest_first(provider, monkeypatch):
    import services.execution.http_client as http_client

    calls: list[tuple[str, str, dict]] = []

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        calls.append((method, url, headers or {}))
        if url.endswith("/Notes/A.md"):
            return {"status_code": 207, "text": _FILEID_REPLY}
        return {"status_code": 207, "text": _VERSIONS_REPLY}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(request("versions", path="Notes/A.md"))

    assert result.status == "SUCCESS"
    assert [v["id"] for v in result.detail["versions"]] == ["1700003600", "1700000000"]
    assert result.detail["versions"][0]["label"] == "Before trip"
    assert result.detail["versions"][1]["size"] == 12
    assert calls[1][:2] == ("PROPFIND", "http://nextcloud.test/remote.php/dav/versions/u/versions/4242")
    assert calls[1][2]["Depth"] == "1"


async def test_versions_without_the_versions_app_is_an_empty_list(provider, monkeypatch):
    import services.execution.http_client as http_client

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        if url.endswith("/Notes/A.md"):
            return {"status_code": 207, "text": _FILEID_REPLY}
        return {"status_code": 404, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(request("versions", path="Notes/A.md"))
    assert result.status == "SUCCESS"
    assert result.detail == {"versions": []}


async def test_version_read_and_restore_use_the_versions_endpoint(provider, monkeypatch):
    import services.execution.http_client as http_client

    calls: list[tuple[str, str, dict]] = []

    async def fake_request(method, url, data=None, headers=None, **kwargs):
        calls.append((method, url, headers or {}))
        if method == "PROPFIND":
            return {"status_code": 207, "text": _FILEID_REPLY}
        if method == "GET":
            return {"status_code": 200, "text": "# A\nCategory: Notes\n\nold"}
        return {"status_code": 201, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)

    read = await note_handler.handle_note(request("version_read", path="Notes/A.md", version="1700000000"))
    assert read.status == "SUCCESS"
    assert read.message.endswith("old")
    assert calls[1][:2] == ("GET", "http://nextcloud.test/remote.php/dav/versions/u/versions/4242/1700000000")

    restored = await note_handler.handle_note(request("version_restore", path="Notes/A.md", version="1700000000"))
    assert restored.status == "SUCCESS"
    method, url, headers = calls[-1]
    assert (method, url) == ("MOVE", "http://nextcloud.test/remote.php/dav/versions/u/versions/4242/1700000000")
    assert headers["Destination"] == "http://nextcloud.test/remote.php/dav/versions/u/restore/target"


async def test_a_version_id_that_is_not_a_timestamp_never_reaches_a_url(provider, monkeypatch):
    import services.execution.http_client as http_client

    calls: list[str] = []

    async def fake_request(method, url, **kwargs):
        calls.append(url)
        return {"status_code": 207, "text": _FILEID_REPLY}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(request("version_restore", path="Notes/A.md", version="../../files/u/x"))
    assert result.status == "FAILURE"
    assert calls == []


async def test_versions_of_a_missing_note_say_so(provider, monkeypatch):
    import services.execution.http_client as http_client

    async def fake_request(method, url, **kwargs):
        return {"status_code": 404, "text": ""}

    monkeypatch.setattr(http_client, "request", fake_request)

    result = await note_handler.handle_note(request("versions", path="Notes/Gone.md"))
    assert result.status == "FAILURE"
    assert "not there" in result.message


async def test_shares_lists_the_people_a_note_is_shared_with(provider):
    provider.ocs_answer = (True, [
        {"id": 7, "share_type": 0, "share_with": "michele", "share_with_displayname": "Michele", "permissions": 3},
        {"id": 8, "share_type": 0, "share_with": "kid", "share_with_displayname": "Kid", "permissions": 1},
        {"id": 9, "share_type": 3, "share_with": None, "permissions": 1},  # a public link is not a person
    ], "")
    result = await note_handler.handle_note(request("shares", path="Notes/A.md"))

    assert result.status == "SUCCESS"
    assert result.detail["shares"] == [
        {"id": "7", "with": "michele", "name": "Michele", "can_edit": True},
        {"id": "8", "with": "kid", "name": "Kid", "can_edit": False},
    ]
    method, endpoint, params, _ = provider.ocs_calls[0]
    assert (method, endpoint) == ("GET", "/ocs/v2.php/apps/files_sharing/api/v1/shares")
    assert params["path"] == "/Notes/A.md"


async def test_share_add_shares_with_one_user_read_only_or_editable(provider):
    provider.ocs_answer = (True, {"id": 11, "share_type": 0, "share_with": "michele", "permissions": 3}, "")
    result = await note_handler.handle_note(request("share_add", path="Notes/A.md", share_with="michele", can_edit=True))
    assert result.status == "SUCCESS"
    assert result.detail["share"]["can_edit"] is True
    _, endpoint, _, data = provider.ocs_calls[0]
    assert endpoint == "/ocs/v2.php/apps/files_sharing/api/v1/shares"
    assert data == {"path": "/Notes/A.md", "shareType": "0", "shareWith": "michele", "permissions": "3"}

    await note_handler.handle_note(request("share_add", path="Notes/A.md", share_with="kid"))
    assert provider.ocs_calls[1][3]["permissions"] == "1"


async def test_share_add_refuses_nobody_or_yourself(provider):
    for who in ("", "u"):
        result = await note_handler.handle_note(request("share_add", path="Notes/A.md", share_with=who))
        assert result.status == "FAILURE"
    assert provider.ocs_calls == []


async def test_share_remove_deletes_by_numeric_id_only(provider):
    result = await note_handler.handle_note(request("share_remove", share_id="11"))
    assert result.status == "SUCCESS"
    assert provider.ocs_calls[0][:2] == ("DELETE", "/ocs/v2.php/apps/files_sharing/api/v1/shares/11")

    bad = await note_handler.handle_note(request("share_remove", share_id="11/../../x"))
    assert bad.status == "FAILURE"
    assert len(provider.ocs_calls) == 1


async def test_sharees_offers_other_users_once_and_never_yourself(provider):
    provider.ocs_answer = (True, {
        "exact": {"users": [{"label": "Michele", "value": {"shareType": 0, "shareWith": "michele"}}]},
        "users": [
            {"label": "Michele", "value": {"shareType": 0, "shareWith": "michele"}},
            {"label": "Ada", "value": {"shareType": 0, "shareWith": "ada"}},
            {"label": "Me", "value": {"shareType": 0, "shareWith": "u"}},
        ],
    }, "")
    result = await note_handler.handle_note(request("sharees"))
    assert result.status == "SUCCESS"
    assert result.detail["people"] == [{"id": "ada", "name": "Ada"}, {"id": "michele", "name": "Michele"}]
    assert provider.ocs_calls[0][2]["itemType"] == "file"


async def test_a_refused_share_reports_nextcloud_s_reason(provider):
    provider.ocs_answer = (False, None, "Sharing is disabled for this user")
    result = await note_handler.handle_note(request("share_add", path="Notes/A.md", share_with="michele"))
    assert result.status == "FAILURE"
    assert result.message == "Sharing is disabled for this user"
