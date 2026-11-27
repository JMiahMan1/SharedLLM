"""StorageFileReadRequest reads a Nextcloud PDF as text, not as binary garbage."""
import pytest
from pdf_fixtures import make_encrypted_pdf, make_pdf

from services.execution.handlers import storage
from services.execution.schemas import StorageFileReadRequest, UserContext


@pytest.fixture
def nextcloud(monkeypatch):
    """Serve one file body; records the URL asked for."""
    state: dict = {"status": 200, "type": "application/pdf", "body": b"", "urls": []}
    monkeypatch.setattr(storage, "resolve_credentials", lambda ctx: ("https://cloud.test", "anna", "pw"))

    async def fetch_bytes(url, **kw):
        state["urls"].append(url)
        return state["status"], state["type"], state["body"]

    monkeypatch.setattr(storage, "fetch_bytes", fetch_bytes)
    return state


def _req(path: str, **kw) -> StorageFileReadRequest:
    return StorageFileReadRequest(user_context=UserContext(user="anna"), path=path, **kw)


async def test_storage_read_returns_a_pdfs_text_with_page_markers(nextcloud):
    nextcloud["body"] = make_pdf(["Chapter one", "Chapter two"], title="Field Guide")

    result = await storage.handle_storage_read(_req("/Documents/guide.pdf"))

    assert result.status == "SUCCESS"
    content = result.detail["content"]
    assert "--- Page 1 ---\nChapter one" in content and "--- Page 2 ---\nChapter two" in content
    assert result.detail["pages"] == "pages 1-2 of 2"
    assert result.detail["title"] == "Field Guide"
    assert result.detail["path"] == "/Documents/guide.pdf"
    assert "pages 1-2 of 2" in result.message


async def test_storage_read_detects_a_pdf_by_its_bytes(nextcloud):
    nextcloud["type"] = "application/octet-stream"
    nextcloud["body"] = make_pdf(["by the bytes"])

    result = await storage.handle_storage_read(_req("/Documents/scan"))

    assert result.status == "SUCCESS"
    assert "by the bytes" in result.detail["content"]
    assert result.detail["title"] == "scan"


async def test_storage_read_start_page_continues_a_pdf(nextcloud):
    nextcloud["body"] = make_pdf([f"page text {n}" for n in range(1, 6)])

    result = await storage.handle_storage_read(_req("/Documents/book.pdf", start_page=4))

    assert result.status == "SUCCESS"
    assert result.detail["pages"] == "pages 4-5 of 5"
    assert "--- Page 3 ---" not in result.detail["content"]
    assert "page text 4" in result.detail["content"]


async def test_storage_read_a_long_pdf_says_where_to_continue(nextcloud, monkeypatch):
    monkeypatch.setattr(storage, "PDF_READ_MAX_CHARS", 60)
    nextcloud["body"] = make_pdf([f"page text {n} " + "y" * 20 for n in range(1, 6)])

    result = await storage.handle_storage_read(_req("/Documents/book.pdf"))

    assert result.status == "SUCCESS"
    assert result.detail["first_page"] == 1
    assert result.detail["last_page"] < 5
    assert result.detail["next_start_page"] == result.detail["last_page"] + 1


async def test_storage_read_a_scanned_pdf_says_so(nextcloud):
    nextcloud["body"] = make_pdf([""])

    result = await storage.handle_storage_read(_req("/Documents/scan.pdf"))

    assert result.status == "SUCCESS"
    assert "no text layer" in result.detail["note"]


async def test_storage_read_an_encrypted_pdf_fails_clearly(nextcloud):
    nextcloud["body"] = make_encrypted_pdf()

    result = await storage.handle_storage_read(_req("/Documents/locked.pdf"))

    assert result.status == "FAILURE"
    assert "encrypted" in result.message


async def test_storage_read_a_text_file_is_unchanged(nextcloud):
    nextcloud["type"] = "text/plain"
    nextcloud["body"] = b"Milk\nEggs\n"

    result = await storage.handle_storage_read(_req("/Documents/list.txt"))

    assert result.status == "SUCCESS"
    assert result.detail == {"content": "Milk\nEggs\n", "path": "/Documents/list.txt"}
    assert nextcloud["urls"][0].endswith("/Documents/list.txt")


async def test_storage_read_missing_file(nextcloud):
    nextcloud["status"] = 404

    result = await storage.handle_storage_read(_req("/Documents/none.pdf"))

    assert result.status == "FAILURE"
    assert "not found" in result.message
