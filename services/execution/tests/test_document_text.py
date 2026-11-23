import pytest


@pytest.mark.asyncio
async def test_extract_pdf_text_uses_pdftotext(mocker):
    from services.execution.document_text import extract_document_text

    mocker.patch("services.execution.document_text.shutil.which", return_value="/usr/bin/pdftotext")

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"Week 2: Scripture\n\nPsalm 139", b""

        async def kill(self):
            pass

        async def wait(self):
            pass

    mock_create = mocker.patch("services.execution.document_text.asyncio.create_subprocess_exec", return_value=FakeProc())
    mocker.patch("services.execution.document_text.os.path.exists", return_value=True)
    mocker.patch("services.execution.document_text.os.path.getsize", return_value=1024)

    text = await extract_document_text("/ws/Week 2 - Scripture.pdf")
    assert "Week 2: Scripture" in text
    assert "Psalm 139" in text
    # pdftotext must run with -layout to preserve headings/list columns.
    args, _kwargs = mock_create.call_args
    assert args[0] == "pdftotext"
    assert "-layout" in args


@pytest.mark.asyncio
async def test_extract_epub_uses_pandoc(mocker):
    from services.execution.document_text import extract_document_text

    mocker.patch("services.execution.document_text.shutil.which", return_value="/usr/bin/pandoc")
    mocker.patch("services.execution.document_text.os.path.exists", return_value=True)
    mocker.patch("services.execution.document_text.os.path.getsize", return_value=2048)

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"Chapter One.\n\nIn the beginning.", b""

        async def kill(self):
            pass

        async def wait(self):
            pass

    mock_create = mocker.patch("services.execution.document_text.asyncio.create_subprocess_exec", return_value=FakeProc())
    text = await extract_document_text("/ws/book.epub")
    assert "Chapter One." in text
    args, _kwargs = mock_create.call_args
    assert args[0] == "pandoc"
    assert "-t" in args and "plain" in args
    assert not any("split-level" in a for a in args)


@pytest.mark.asyncio
async def test_extract_docx_via_pandoc_fallback(mocker):
    from services.execution.document_text import extract_document_text

    mocker.patch("services.execution.document_text.shutil.which", return_value="/usr/bin/pandoc")
    mocker.patch("services.execution.document_text.os.path.exists", return_value=True)
    mocker.patch("services.execution.document_text.os.path.getsize", return_value=2048)

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return b"Report body text.", b""

        async def kill(self):
            pass

        async def wait(self):
            pass

    mock_create = mocker.patch("services.execution.document_text.asyncio.create_subprocess_exec", return_value=FakeProc())
    text = await extract_document_text("/ws/report.docx")
    assert text == "Report body text."
    assert mock_create.call_args.args[0].startswith("pandoc")


def test_plain_text_returns_directly(tmp_path):
    from services.execution.document_text import extract_document_text, is_document, is_text_file

    txt = tmp_path / "narration.txt"
    txt.write_text("Plain narration text.")

    assert is_text_file(str(txt))
    assert not is_document(str(txt))
    # A plain .txt must be read without invoking any converter.
    import asyncio

    got = asyncio.run(extract_document_text(str(txt)))
    assert got == "Plain narration text."


def test_is_document_pdf_epub_docx():
    from services.execution.document_text import is_document, is_text_file

    assert is_document("Week 2 - Scripture.pdf")
    assert not is_text_file("Week 2 - Scripture.pdf")
    assert is_document("book.epub")
    assert is_document("report.docx")


@pytest.mark.asyncio
async def test_extract_missing_converter_raises_clear_error(mocker):
    from services.execution.document_text import extract_document_text

    mocker.patch("services.execution.document_text.shutil.which", return_value=None)
    mocker.patch("services.execution.document_text.os.path.exists", return_value=True)
    mocker.patch("services.execution.document_text.os.path.getsize", return_value=2048)

    with pytest.raises(RuntimeError, match="pdftotext is not installed"):
        await extract_document_text("/ws/some.pdf")


def test_workspace_read_extracts_pdf_text(mocker, tmp_path, monkeypatch):
    """WorkspaceFileReadRequest on a PDF returns the extracted text layer."""
    import os

    from fastapi.testclient import TestClient

    os.environ.setdefault("INTERNAL_SECRET", "test-secret")
    os.environ.setdefault("EXECUTION_EXTERNAL_HOST", "localhost")
    os.environ.setdefault("DEVICE_REGISTRY_PATH", ":memory:")
    from services.config import INTERNAL_SECRET
    from services.execution.main import app

    pdf = tmp_path / "Week 2 - Scripture.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfake")

    async def fake_extract(path):
        return "Week 2: Scripture\n\nPsalm 139"

    mocker.patch(
        "services.execution.document_text.extract_document_text",
        side_effect=fake_extract,
    )
    mocker.patch(
        "services.execution.handlers.workspace._resolve_workspace_info",
        return_value=(str(tmp_path), {"capabilities": {"read": True}}),
    )
    mocker.patch("services.execution.handlers.workspace._require_capability", return_value=None)

    client = TestClient(app)
    resp = client.post(
        "/execute/workspace_file_read",
        headers={"X-Internal-Secret": INTERNAL_SECRET},
        json={
            "user_context": {"user": "testuser", "is_admin": True},
            "workspace_id": "ws-abc",
            "path": "Week 2 - Scripture.pdf",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SUCCESS"
    assert body["detail"]["content"] == "Week 2: Scripture\n\nPsalm 139"
    assert body["detail"]["source"] == "pdf-extract"
