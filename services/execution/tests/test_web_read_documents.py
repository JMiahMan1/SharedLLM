"""WebReadRequest: PDFs, text files and downloads without a browser; main-content HTML.

The documents come from a local aiohttp server (no network); Playwright is
replaced with a fake so no browser starts.
"""
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from pdf_fixtures import make_encrypted_pdf, make_pdf

from services.execution import personal_data
from services.execution.handlers import browser
from services.execution.schemas import UserContext, WebReadRequest

LONG_PAGE = "Deuteronomy thirty two eight sons of God divine council. " * 6  # ~330 chars


def _req(url: str, **kw) -> WebReadRequest:
    return WebReadRequest(user_context=UserContext(user="tester"), url=url, **kw)


@pytest.fixture
async def docs_server():
    """A local server; tests register bodies with ``serve(path, body, content_type)``."""
    routes: dict[str, tuple[bytes, str, dict]] = {}

    async def handler(request: web.Request) -> web.StreamResponse:
        if request.path not in routes:
            return web.Response(status=404, text="missing")
        body, ctype, headers = routes[request.path]
        return web.Response(body=body, headers={"Content-Type": ctype, **headers})

    app = web.Application()
    app.router.add_route("GET", "/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()

    def serve(path: str, body: bytes, content_type: str, headers: dict | None = None) -> str:
        routes[path] = (body, content_type, headers or {})
        return str(server.make_url(path))

    yield serve
    await server.close()


class _FakePage:
    def __init__(self, *, html: str = "", title: str = "", goto_error: str | None = None):
        self._html, self._title, self._error = html, title, goto_error
        self.context = self

    async def add_cookies(self, cookies):
        return None

    async def goto(self, url, **kw):
        if self._error:
            raise Exception(self._error)

    async def content(self):
        return self._html

    async def title(self):
        return self._title


class _FakePlaywright:
    def __init__(self, page: _FakePage):
        self.page = page
        self.launched = 0
        self.chromium = self

    async def launch(self, **kw):
        self.launched += 1
        return self

    async def new_page(self):
        return self.page

    async def close(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch_playwright(monkeypatch, page: _FakePage) -> _FakePlaywright:
    fake = _FakePlaywright(page)
    monkeypatch.setattr(browser, "async_playwright", lambda: fake)
    return fake


def _no_browser(monkeypatch) -> _FakePlaywright:
    return _patch_playwright(monkeypatch, _FakePage(goto_error="the browser must not be used"))


async def test_a_pdf_url_returns_its_text_with_page_markers(docs_server, monkeypatch):
    fake = _no_browser(monkeypatch)
    url = docs_server("/DT32BibSac.pdf", make_pdf(["Deuteronomy 32:8", "sons of God"], title="DT32"), "application/pdf")

    result = await browser.handle_web_read(_req(url))

    assert result.status == "SUCCESS", result.message
    assert fake.launched == 0
    content = result.detail["content"]
    assert "--- Page 1 ---" in content and "--- Page 2 ---" in content
    assert "Deuteronomy 32:8" in content and "sons of God" in content
    assert result.detail["title"] == "DT32"
    assert result.detail["pages"] == "pages 1-2 of 2"
    assert "pages 1-2 of 2" in result.message
    assert "next_start_page" not in result.detail


async def test_a_long_pdf_says_which_pages_fit_and_start_page_continues(docs_server, monkeypatch):
    _no_browser(monkeypatch)
    monkeypatch.setattr(browser, "WEB_READ_MAX_CHARS", 200)
    pages = [f"Page body number {n} " + "x" * 40 for n in range(1, 11)]
    url = docs_server("/long.pdf", make_pdf(pages), "application/pdf")

    first = await browser.handle_web_read(_req(url))
    assert first.status == "SUCCESS"
    assert first.detail["first_page"] == 1
    last = first.detail["last_page"]
    assert 1 <= last < 10
    assert first.detail["pages"] == f"pages 1-{last} of 10"
    assert first.detail["next_start_page"] == last + 1
    assert f"start_page={last + 1}" in first.detail["note"]
    assert first.detail["title"] == "long.pdf"

    second = await browser.handle_web_read(_req(url, start_page=last + 1))
    assert second.status == "SUCCESS"
    assert second.detail["first_page"] == last + 1
    assert f"--- Page {last + 1} ---" in second.detail["content"]
    assert "--- Page 1 ---" not in second.detail["content"]

    past = await browser.handle_web_read(_req(url, start_page=11))
    assert past.status == "FAILURE"
    assert "10 pages" in past.message


async def test_a_download_with_no_pdf_suffix_falls_back_to_a_direct_fetch(docs_server, monkeypatch):
    url = docs_server("/get", make_pdf(["Heiser on the divine council"]), "application/octet-stream") + "?id=42"
    fake = _patch_playwright(monkeypatch, _FakePage(goto_error="Page.goto: Download is starting"))

    result = await browser.handle_web_read(_req(url))

    assert fake.launched == 1
    assert result.status == "SUCCESS", result.message
    assert "Heiser on the divine council" in result.detail["content"]
    assert result.detail["pages"] == "page 1 of 1"


async def test_a_scanned_pdf_is_a_success_with_a_note(docs_server, monkeypatch):
    _no_browser(monkeypatch)
    url = docs_server("/scan.pdf", make_pdf(["", ""]), "application/pdf")

    result = await browser.handle_web_read(_req(url))

    assert result.status == "SUCCESS"
    assert "scanned image with no text layer" in result.detail["note"]
    assert "scanned image" in result.message


async def test_an_encrypted_pdf_is_a_clear_failure(docs_server, monkeypatch):
    _no_browser(monkeypatch)
    url = docs_server("/locked.pdf", make_encrypted_pdf(), "application/pdf")

    result = await browser.handle_web_read(_req(url))

    assert result.status == "FAILURE"
    assert "encrypted" in result.message


async def test_a_text_file_is_read_without_a_browser(docs_server, monkeypatch):
    url = docs_server("/notes.txt", "Plain words, caf\u00e9.".encode(), "text/plain; charset=utf-8")
    fake = _patch_playwright(monkeypatch, _FakePage(goto_error="Page.goto: Download is starting"))

    result = await browser.handle_web_read(_req(url))

    assert fake.launched == 1
    assert result.status == "SUCCESS"
    assert result.detail["content"] == "Plain words, caf\u00e9."
    assert result.detail["content_type"] == "text/plain"


async def test_an_image_fails_naming_its_content_type(docs_server, monkeypatch):
    url = docs_server("/photo.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")
    _patch_playwright(monkeypatch, _FakePage(goto_error="Page.goto: Download is starting"))

    result = await browser.handle_web_read(_req(url))

    assert result.status == "FAILURE"
    assert "image/png" in result.message


async def test_the_size_cap_stops_a_huge_body(docs_server, monkeypatch):
    _no_browser(monkeypatch)
    monkeypatch.setattr(browser, "WEB_READ_MAX_BYTES", 1024)
    url = docs_server("/huge.pdf", b"%PDF-1.4\n" + b"0" * 200_000, "application/pdf")

    result = await browser.handle_web_read(_req(url))

    assert result.status == "FAILURE"
    assert "read limit" in result.message


async def test_the_size_cap_also_stops_a_body_with_no_length(monkeypatch):
    """A chunked body has no Content-Length: the reader counts as it goes."""
    async def chunked(request):
        resp = web.StreamResponse(headers={"Content-Type": "application/pdf"})
        resp.enable_chunked_encoding()
        await resp.prepare(request)
        for _ in range(50):
            await resp.write(b"0" * 4096)
        await resp.write_eof()
        return resp

    app = web.Application()
    app.router.add_get("/stream.pdf", chunked)
    server = TestServer(app)
    await server.start_server()
    try:
        monkeypatch.setattr(browser, "WEB_READ_MAX_BYTES", 10_000)
        doc = await browser._fetch_document(str(server.make_url("/stream.pdf")))
    finally:
        await server.close()

    assert doc.too_large
    assert doc.data == b""


async def test_html_returns_the_main_article_with_its_title(monkeypatch):
    html = (
        "<html><head><title>Divine Council</title></head><body>"
        "<nav><a href='/'>Home</a> <a href='/about'>About</a> NAVIGATION-JUNK</nav>"
        f"<article><h1>Divine Council</h1><p>{LONG_PAGE}</p><p>{LONG_PAGE}</p></article>"
        "<footer>FOOTER-JUNK copyright</footer></body></html>"
    )
    _patch_playwright(monkeypatch, _FakePage(html=html, title="Divine Council"))

    result = await browser.handle_web_read(_req("https://example.test/article"))

    assert result.status == "SUCCESS"
    assert result.detail["extraction"] == "main_content"
    content = result.detail["content"]
    assert content.startswith("# Divine Council")
    assert "sons of God divine council" in content
    assert "NAVIGATION-JUNK" not in content
    assert result.detail["title"] == "Divine Council"


async def test_html_with_little_main_content_falls_back_to_the_whole_page(monkeypatch):
    html = "<html><head><title>Tiny</title></head><body><p>Short <a href='https://x.test'>link</a>.</p></body></html>"
    _patch_playwright(monkeypatch, _FakePage(html=html, title="Tiny"))

    result = await browser.handle_web_read(_req("https://example.test/tiny"))

    assert result.status == "SUCCESS"
    assert result.detail["extraction"] == "full_page"
    assert "[link](https://x.test)" in result.detail["content"]
    assert result.detail["content"].startswith("# Tiny")


async def test_a_pdf_url_that_serves_a_page_is_read_by_the_browser(docs_server, monkeypatch):
    url = docs_server("/paper.pdf", b"<!doctype html><html><body>login</body></html>", "text/html")
    fake = _patch_playwright(monkeypatch, _FakePage(html="<html><body><p>Sign in</p></body></html>", title="Sign in"))

    result = await browser.handle_web_read(_req(url))

    assert fake.launched == 1
    assert result.status == "SUCCESS"
    assert result.detail["title"] == "Sign in"


class _FakeProvider:
    def __init__(self, existing: set[str] | None = None, accept: bool = True):
        self.existing = set(existing or ())
        self.accept = accept
        self.dirs: list[str] = []
        self.uploads: dict[str, tuple[bytes, str]] = {}

    def sanitize_filename(self, value, fallback):
        return value or fallback

    async def ensure_directory(self, path):
        self.dirs.append(path)

    async def upload_file(self, path, data, content_type):
        self.uploads[path] = (data, content_type)
        return self.accept


def _patch_storage(monkeypatch, provider):
    monkeypatch.setattr(personal_data, "resolve_personal_data_provider", lambda ctx: provider)

    async def exists(prov, path):
        return path in prov.existing

    monkeypatch.setattr(browser, "_storage_path_exists", exists)


async def test_save_stores_the_file_under_downloads_and_returns_the_path(docs_server, monkeypatch):
    _no_browser(monkeypatch)
    data = make_pdf(["Deuteronomy 32:8"])
    url = docs_server("/DT32BibSac.pdf", data, "application/pdf")
    provider = _FakeProvider(existing={"Downloads/DT32BibSac.pdf"})
    _patch_storage(monkeypatch, provider)

    result = await browser.handle_web_read(_req(url, save=True))

    assert result.status == "SUCCESS"
    assert result.detail["saved_path"] == "Downloads/DT32BibSac (2).pdf"
    assert provider.uploads["Downloads/DT32BibSac (2).pdf"] == (data, "application/pdf")
    assert provider.dirs == ["Downloads"]
    assert "Saved to Downloads/DT32BibSac (2).pdf" in result.message
    assert "Deuteronomy 32:8" in result.detail["content"]


async def test_save_keeps_a_file_with_no_text(docs_server, monkeypatch):
    url = docs_server("/files/archive.zip", b"PK\x03\x04" + b"0" * 32, "application/zip")
    _patch_playwright(monkeypatch, _FakePage(goto_error="Page.goto: Download is starting"))
    provider = _FakeProvider()
    _patch_storage(monkeypatch, provider)

    result = await browser.handle_web_read(_req(url, save=True))

    assert result.status == "SUCCESS"
    assert result.detail["saved_path"] == "Downloads/archive.zip"
    assert "application/zip" in result.message


async def test_save_without_a_storage_account_still_reads(docs_server, monkeypatch):
    _no_browser(monkeypatch)
    url = docs_server("/paper.pdf", make_pdf(["still readable"]), "application/pdf")
    monkeypatch.setattr(personal_data, "resolve_personal_data_provider", lambda ctx: None)

    result = await browser.handle_web_read(_req(url, save=True))

    assert result.status == "SUCCESS"
    assert "still readable" in result.detail["content"]
    assert "saved_path" not in result.detail
    assert "No Nextcloud account" in result.detail["save_error"]


def test_the_filename_comes_from_content_disposition_then_the_url():
    assert browser._filename_from("https://x.test/a/b.pdf", None) == "b.pdf"
    assert browser._filename_from("https://x.test/a/My%20Paper.pdf", None) == "My Paper.pdf"
    assert browser._filename_from("https://x.test/get", 'attachment; filename="real.pdf"') == "real.pdf"
    assert browser._filename_from("https://x.test/get", "attachment; filename*=UTF-8''caf%C3%A9.pdf") == "caf\u00e9.pdf"
    assert browser._filename_from("https://x.test/", None) == "download"
