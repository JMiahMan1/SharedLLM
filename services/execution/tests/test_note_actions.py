import os

os.environ.setdefault("INTERNAL_SECRET", "test-secret")
os.environ.setdefault("FERNET_KEY", "bW9ja2VkLWtleS1mb3ItdGVzdGluZy1wdXJwb3NlcyE=")

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
