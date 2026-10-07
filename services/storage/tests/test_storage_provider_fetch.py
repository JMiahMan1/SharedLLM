import base64

import pytest
from fastapi.testclient import TestClient

from services.storage.main import app
from services.storage.providers import ProviderConfig, StorageProvider

client = TestClient(app)

SECRET_HEADERS = {"X-Internal-Secret": "test-secret"}

BODY = b"%PDF-1.4 a study bible, or at least some bytes \x00\x01\x02"


class FetchProvider(StorageProvider):
    def __init__(self, payload, boom=None):
        self._payload = payload
        self._boom = boom
        self.asked = []

    def list_entries(self, path="/", recursive=False):
        return []

    async def get_bytes(self, path):
        self.asked.append(path)
        if self._boom:
            raise self._boom
        return self._payload

    def get_content(self, path, encoding="utf-8"):
        raise AssertionError("get_content decodes as text and would corrupt a PDF")

    def write_content(self, path, content, create_parents=True, verify=True, is_binary=False):
        raise AssertionError("a fetch must never write")

    def upload_directory(self, remote_path, local_path, excludes=None):
        return {"status": "SUCCESS", "files_uploaded": 0}


def config(kind="nextcloud"):
    return {"provider": ProviderConfig(kind=kind, settings={}).model_dump()}


def patch_provider(monkeypatch, provider):
    monkeypatch.setattr("services.storage.main.build_provider", lambda request: provider)
    return provider


def test_a_document_comes_back_as_base64_bytes(monkeypatch):
    provider = patch_provider(monkeypatch, FetchProvider(BODY))
    response = client.post(
        "/providers/fetch",
        headers=SECRET_HEADERS,
        json={**config(), "path": "Books/Text/Bible.epub"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCESS"
    assert body["path"] == "Books/Text/Bible.epub"
    assert body["size"] == len(BODY)
    assert base64.b64decode(body["content_b64"]) == BODY
    assert provider.asked == ["Books/Text/Bible.epub"]


def test_the_limit_is_refused_with_both_numbers_rather_than_truncating(monkeypatch):
    patch_provider(monkeypatch, FetchProvider(BODY))
    response = client.post(
        "/providers/fetch",
        headers=SECRET_HEADERS,
        json={**config(), "path": "Bible.epub", "max_bytes": 4},
    )
    assert response.status_code == 413
    detail = response.json()["detail"]
    assert str(len(BODY)) in detail
    assert "4 byte limit" in detail


def test_a_limit_of_zero_is_refused_as_a_mistake(monkeypatch):
    patch_provider(monkeypatch, FetchProvider(BODY))
    response = client.post(
        "/providers/fetch",
        headers=SECRET_HEADERS,
        json={**config(), "path": "Bible.epub", "max_bytes": 0},
    )
    assert response.status_code == 422
    assert "positive number of bytes" in response.json()["detail"]


def test_a_provider_that_cannot_read_bytes_says_all_three_ways_it_can_fail(monkeypatch):
    patch_provider(monkeypatch, FetchProvider(None))
    response = client.post(
        "/providers/fetch",
        headers=SECRET_HEADERS,
        json={**config(), "path": "Books/Missing.epub"},
    )
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "Books/Missing.epub" in detail
    assert "missing" in detail
    assert "credentials" in detail
    assert "cannot return binary" in detail


def test_a_provider_that_raises_is_a_server_fault(monkeypatch):
    patch_provider(monkeypatch, FetchProvider(None, boom=RuntimeError("webdav exploded")))
    response = client.post(
        "/providers/fetch",
        headers=SECRET_HEADERS,
        json={**config(), "path": "Bible.epub"},
    )
    assert response.status_code == 500


def test_the_fetch_needs_the_internal_secret(monkeypatch):
    patch_provider(monkeypatch, FetchProvider(BODY))
    response = client.post("/providers/fetch", json={**config(), "path": "Bible.epub"})
    assert response.status_code == 403


def test_a_fetch_without_a_path_is_rejected_by_validation(monkeypatch):
    patch_provider(monkeypatch, FetchProvider(BODY))
    response = client.post("/providers/fetch", headers=SECRET_HEADERS, json=config())
    assert response.status_code == 422


async def test_a_provider_that_cannot_give_bytes_declines_instead_of_guessing():
    """The base answer is None: a provider with no byte representation says so
    rather than handing back something it made up."""
    assert await StorageProvider.get_bytes(object(), "x.epub") is None


async def test_the_calibre_shelf_serves_its_extracted_text_as_bytes():
    """A shelf entry is a ``.txt`` document, so its bytes are its UTF-8 text --
    that is what lets ``/providers/fetch`` serve Raven's ``fetch_text``. A book
    with no extractable text still declines with None."""
    from services.storage.providers_impl.calibre import CalibreStorageProvider

    class _Shelf:
        def __init__(self, text):
            self._text = text

        async def get_content(self, path):
            return self._text

    assert await CalibreStorageProvider.get_bytes(_Shelf("Prose."), "x.txt") == b"Prose."
    assert await CalibreStorageProvider.get_bytes(_Shelf(None), "x.txt") is None
