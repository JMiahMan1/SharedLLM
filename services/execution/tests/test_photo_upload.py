"""The photo store: a thumbnail even when Nextcloud is not connected.

The Garden's growth journal is what people look at. A household with no
Nextcloud must still get the small picture on the plant's timeline -- the old
handler failed outright, so the journal stayed empty for them.
"""
from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from services.execution.handlers import storage

#: A real 1x1 PNG, base64, so the size and magic-byte checks are honest.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _req(**over) -> SimpleNamespace:
    body = {
        "user_context": {},
        "folder": "Garden/Photos/p-1",
        "filename": "photo.png",
        "data": base64.b64encode(PNG_BYTES).decode("ascii"),
        "content_type": "image/png",
        "thumbnail_max_px": 480,
    }
    body.update(over)
    return SimpleNamespace(**body)


@pytest.fixture()
def thumbnail(monkeypatch):
    """Stand in for Pillow: these tests are about the decision, not the resize."""
    monkeypatch.setattr(storage, "_jpeg_thumbnail", lambda data, max_px: b"thumb-bytes")


class Provider:
    def __init__(self, *, uploads: bool = True, raises: Exception | None = None):
        self.uploads = uploads
        self.raises = raises
        self.calls: list[tuple] = []

    def sanitize_filename(self, name, fallback):
        return name or fallback

    async def ensure_directory(self, folder):
        self.calls.append(("ensure", folder))

    async def upload_file(self, path, data, content_type):
        if self.raises:
            raise self.raises
        self.calls.append(("upload", path, content_type))
        return self.uploads


def test_a_photo_with_no_nextcloud_still_returns_a_thumbnail(monkeypatch, thumbnail):
    monkeypatch.setattr(storage, "resolve_personal_data_provider", lambda _ctx: None)

    result = __import__("asyncio").run(storage.handle_photo_upload(_req()))

    assert result.status == "SUCCESS"
    assert result.detail["path"] is None
    assert "thumbnail_base64" in result.detail
    assert base64.b64decode(result.detail["thumbnail_base64"]) == b"thumb-bytes"
    assert "No Nextcloud" in result.detail["warning"]


def test_a_stored_photo_keeps_its_path_and_thumbnail(monkeypatch, thumbnail):
    provider = Provider()
    monkeypatch.setattr(storage, "resolve_personal_data_provider", lambda _ctx: provider)

    result = __import__("asyncio").run(storage.handle_photo_upload(_req()))

    assert result.status == "SUCCESS"
    assert result.detail["path"] == "Garden/Photos/p-1/photo.png"
    assert "warning" not in result.detail
    assert ("upload", "Garden/Photos/p-1/photo.png", "image/png") in provider.calls


def test_an_upload_that_fails_degrades_to_the_thumbnail(monkeypatch, thumbnail):
    monkeypatch.setattr(
        storage, "resolve_personal_data_provider", lambda _ctx: Provider(uploads=False)
    )

    result = __import__("asyncio").run(storage.handle_photo_upload(_req()))

    assert result.status == "SUCCESS"
    assert result.detail["path"] is None
    assert "thumbnail_base64" in result.detail
    assert result.detail["warning"]


def test_an_upload_that_raises_degrades_to_the_thumbnail(monkeypatch, thumbnail):
    monkeypatch.setattr(
        storage,
        "resolve_personal_data_provider",
        lambda _ctx: Provider(raises=RuntimeError("nextcloud is down")),
    )

    result = __import__("asyncio").run(storage.handle_photo_upload(_req()))

    assert result.status == "SUCCESS"
    assert result.detail["path"] is None
    assert "thumbnail_base64" in result.detail


def test_an_empty_upload_is_still_a_failure(monkeypatch):
    monkeypatch.setattr(storage, "resolve_personal_data_provider", lambda _ctx: None)

    result = __import__("asyncio").run(storage.handle_photo_upload(_req(data="")))

    assert result.status == "FAILURE"
    assert "empty" in result.message.lower()


def test_a_folder_is_required(monkeypatch):
    monkeypatch.setattr(storage, "resolve_personal_data_provider", lambda _ctx: None)

    result = __import__("asyncio").run(storage.handle_photo_upload(_req(folder="")))

    assert result.status == "FAILURE"
    assert "folder" in result.message.lower()
