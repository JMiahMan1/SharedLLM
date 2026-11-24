"""OcrRequest reads PDFs: the text layer when there is one, the pages as images when not.

Every PDF is built in the test (pypdf for text, Pillow for a scanned page); no
household file and no vision model are used.
"""
import base64
import io
from pathlib import Path

import pytest

from services.execution import pdf_text
from services.execution.handlers import ocr as ocr_mod
from services.execution.schemas import OcrRequest
from services.execution.tests.pdf_fixtures import make_pdf


def scanned_pdf(pages: int = 2) -> bytes:
    """A PDF whose pages are only pictures, the way a scanner makes one."""
    from PIL import Image

    images = [Image.new("RGB", (200, 260), color=(240, 240, 230 - 20 * i)) for i in range(pages)]
    buf = io.BytesIO()
    images[0].save(buf, "PDF", save_all=True, append_images=images[1:])
    return buf.getvalue()


def _req(**overrides):
    fields = {"user_context": {"user": "default", "is_admin": True}, "workspace_id": "Test", "image_path": "doc.pdf"}
    fields.update(overrides)
    return OcrRequest(**fields)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    async def resolve(ws, uc):
        return str(tmp_path), {}

    monkeypatch.setattr(ocr_mod, "_resolve_workspace_info", resolve)
    return tmp_path


@pytest.fixture
def vision(monkeypatch):
    seen: list[dict] = []

    async def fake(path, user_context=None, proxy_url=None, model=None, task="general", context=None):
        seen.append({"path": path, "task": task, "bytes": Path(path).read_bytes()[:8]})
        return {"full_text": f"read {len(seen)}", "headline": "", "subtext": "", "badge": ""}

    monkeypatch.setattr(ocr_mod.vision_ocr, "vision_ocr_screenshot", fake)
    return seen


@pytest.mark.asyncio
async def test_a_pdf_with_a_text_layer_is_read_exactly_without_the_vision_model(workspace, vision):
    (workspace / "doc.pdf").write_bytes(make_pdf(["Grace and peace", "to you"], title="Letter"))
    result = await ocr_mod.handle_ocr(_req())
    assert result.status == "SUCCESS"
    assert "Grace and peace" in result.detail["full_text"] and "to you" in result.detail["full_text"]
    assert result.detail["source"] == "pdf-text-layer" and result.detail["headline"] == "Letter"
    assert vision == []


@pytest.mark.asyncio
async def test_a_scanned_pdf_has_its_pages_read_by_the_vision_model(workspace, vision, monkeypatch):
    monkeypatch.setattr(pdf_text, "_pdftoppm_pages", lambda *a, **k: [])  # as if poppler were missing
    (workspace / "doc.pdf").write_bytes(scanned_pdf(2))
    result = await ocr_mod.handle_ocr(_req())
    assert result.status == "SUCCESS", result.message
    assert result.detail["source"] == "pdf-pages-ocr"
    assert result.detail["page_count"] == 2 and result.detail["last_page"] == 2
    assert "--- Page 1 ---\nread 1" in result.detail["full_text"] and "--- Page 2 ---\nread 2" in result.detail["full_text"]
    assert len(vision) == 2 and not any(v["bytes"].startswith(b"%PDF") for v in vision)
    assert all(not Path(v["path"]).exists() for v in vision), "page images are temporary"


@pytest.mark.asyncio
async def test_a_long_scan_is_read_a_few_pages_at_a_time(workspace, vision, monkeypatch):
    monkeypatch.setattr(pdf_text, "_pdftoppm_pages", lambda *a, **k: [])
    monkeypatch.setattr(ocr_mod, "OCR_PDF_MAX_PAGES", 2)
    (workspace / "doc.pdf").write_bytes(scanned_pdf(5))
    first = await ocr_mod.handle_ocr(_req())
    assert (first.detail["first_page"], first.detail["last_page"], first.detail["next_start_page"]) == (1, 2, 3)
    again = await ocr_mod.handle_ocr(_req(start_page=5))
    assert (again.detail["first_page"], again.detail["last_page"]) == (5, 5)
    assert "next_start_page" not in again.detail


@pytest.mark.asyncio
async def test_a_picture_task_on_a_pdf_uses_the_vision_model(workspace, vision, monkeypatch):
    """A recipe card saved as a PDF still needs the picture read, not only its text."""
    made: list[Path] = []

    def fake_render(data, out_dir, *, first_page=1, max_pages=5, dpi=150):
        from PIL import Image

        path = Path(out_dir) / "page-1.png"
        Image.new("RGB", (20, 20)).save(path)
        made.append(path)
        return [(1, str(path))], 1

    monkeypatch.setattr(ocr_mod, "render_pdf_pages", fake_render)
    (workspace / "doc.pdf").write_bytes(make_pdf(["Sugar cookies"]))
    result = await ocr_mod.handle_ocr(_req(task="recipe"))
    assert result.status == "SUCCESS" and vision[0]["task"] == "recipe"
    assert made and not made[0].exists(), "the page image is removed after the call"


@pytest.mark.asyncio
async def test_an_inline_pdf_is_read_too(vision):
    pdf = make_pdf(["Calories 120"])
    result = await ocr_mod.handle_ocr(
        _req(workspace_id=None, image_path=None, image_base64=base64.b64encode(pdf).decode(), task="document")
    )
    assert result.status == "SUCCESS" and "Calories 120" in result.detail["full_text"]


@pytest.mark.asyncio
async def test_an_encrypted_pdf_fails_with_the_reason(workspace, vision):
    from services.execution.tests.pdf_fixtures import make_encrypted_pdf

    (workspace / "doc.pdf").write_bytes(make_encrypted_pdf())
    result = await ocr_mod.handle_ocr(_req())
    assert result.status == "FAILURE" and "password" in result.message


def test_render_uses_each_pages_own_image_without_poppler(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_text, "_pdftoppm_pages", lambda *a, **k: [])
    pages, count = pdf_text.render_pdf_pages(scanned_pdf(3), str(tmp_path), first_page=2, max_pages=5)
    assert count == 3 and [n for n, _ in pages] == [2, 3]
    assert all(Path(p).stat().st_size > 0 for _, p in pages)


def test_render_refuses_a_start_past_the_end(tmp_path):
    with pytest.raises(ValueError, match="past the end"):
        pdf_text.render_pdf_pages(scanned_pdf(1), str(tmp_path), first_page=4)
