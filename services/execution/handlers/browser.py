# services/execution/handlers/browser.py
import asyncio
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from urllib.parse import unquote, urlencode, urlparse

import aiohttp
import html2text

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

try:
    from schemas import ExecutionResult, WebReadRequest, WebSearchRequest
except ImportError:
    from ..schemas import ExecutionResult, WebReadRequest, WebSearchRequest

log = logging.getLogger("execution.browser")

#: Largest document WebReadRequest will download, in bytes. Read once at start.
WEB_READ_MAX_BYTES = int(os.getenv("WEB_READ_MAX_BYTES", str(25 * 1024 * 1024)))
#: What a page or document read returns to the model at most.
WEB_READ_MAX_CHARS = 15000
#: Main-content extraction shorter than this is treated as a miss.
MAIN_CONTENT_MIN_CHARS = 300
_FETCH_TIMEOUT_S = 20
_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DOWNLOADS_FOLDER = "Downloads"

def _is_testing() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules

_searxng_url_cache = None
_searxng_cache_ts = 0.0
_SEARXNG_CACHE_TTL = 300

async def _get_searxng_url() -> str:
    """Resolve SearXNG URL from Identity service global settings (cached)."""
    global _searxng_url_cache, _searxng_cache_ts
    if _searxng_url_cache and (time.time() - _searxng_cache_ts) < _SEARXNG_CACHE_TTL:
        return _searxng_url_cache
    if _is_testing():
        return os.environ.get("SEARXNG_URL", "http://localhost:8080").rstrip("/")
    try:
        from services.config import IDENTITY_SVC_URL, INTERNAL_SECRET
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as client, client.get(
            f"{IDENTITY_SVC_URL}/api/settings",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        ) as resp:
            if resp.status == 200:
                settings_list = await resp.json()
            for item in settings_list:
                if item.get("key") == "searxng_url":
                    url = item.get("value", "").rstrip("/")
                    if url:
                        _searxng_url_cache = url
                        _searxng_cache_ts = time.time()
                        return url
    except Exception:
        pass
    return os.environ.get("SEARXNG_URL", "").rstrip("/") or "http://localhost:8080"

DEFAULT_ENGINES = "google,bing,duckduckgo"
DEFAULT_LANGUAGE = "en"
DEFAULT_CATEGORY = "general"
DEFAULT_SAFESAFERCH = 0
BROADER_ENGINES = "google,bing,duckduckgo,startpage,brave,google news"

async def handle_web_search(req: WebSearchRequest) -> ExecutionResult:
    """
    Performs a web search via the SearXNG JSON API (fast, structured path),
    with a Playwright DOM fallback for the rare case the JSON endpoint is
    unavailable. The instance is configured via the `searxng_url` Identity
    setting (the search instance's base URL).

    The fallback only covers a transport failure. An empty answer whose
    instance reports blocked engines is a FAILURE from the JSON path, because
    scraping that same instance would only re-report the same empty page.
    """
    log.info(f"[browser/search] query='{req.query}' category='{req.category or DEFAULT_CATEGORY}'")

    try:
        result = await _searxng_json_search(req)
        if result:
            return result
    except Exception as e:
        log.warning(f"[browser/search] SearXNG JSON API failed, falling back to Playwright: {e}")

    try:
        return await _playwright_fallback(req)
    except Exception as e:
        log.error(f"[browser/search] All search methods failed: {e}")
        return ExecutionResult(status="FAILURE", message=f"Web search failed: {e!s}", service="web_search")


async def _searxng_json_search(req: WebSearchRequest) -> ExecutionResult | None:
    """
    Primary path: SearXNG JSON API. Fast, structured, no HTML scraping.

    Returns None only for a genuinely empty answer: the caller then has
    something worth scraping. When the instance itself reports unresponsive
    engines the answer is not empty but broken, and returning None would send
    the Playwright fallback back to the same broken instance, which can only
    scrape another empty page and report success. That case is returned as a
    FAILURE naming each engine and its reason, after one retry with a broader
    engine list in case only the default set was suspended.
    """
    searxng_url = await _get_searxng_url()
    params = {
        "q": req.query,
        "format": "json",
        "categories": req.category or DEFAULT_CATEGORY,
        "language": req.language or DEFAULT_LANGUAGE,
        "safesearch": req.safesearch if req.safesearch is not None else DEFAULT_SAFESAFERCH,
        "pageno": req.pageno or 1,
    }
    # Default to a tested set of engines so an unconfigured instance still
    # searches something useful; an explicit req.engines overrides this.
    params["engines"] = req.engines or DEFAULT_ENGINES
    if req.time_range:
        params["time_range"] = req.time_range

    url = f"{searxng_url}/search?{urlencode(params)}"
    log.info(f"[browser/search] SearXNG JSON search: {url}")

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12.0)) as client, client.get(url) as resp:
            resp.raise_for_status()
            data = await resp.json()
    except Exception:
        # Surface to the Playwright fallback; the JSON endpoint being down or
        # returning non-JSON is exactly the rare case it exists for.
        raise

    results = []
    for r in data.get("results", []):
        engines = r.get("engines")
        if isinstance(engines, list):
            engines = ", ".join(engines)
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", ""),
            "engine": engines or "",
            "publishedDate": r.get("publishedDate") or r.get("pubdate") or "",
        })

    if not results:
        unresponsive = data.get("unresponsive_engines", [])
        if unresponsive and not _is_testing():
            params["engines"] = BROADER_ENGINES
            new_url = f"{searxng_url}/search?{urlencode(params)}"
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=12.0)
                ) as client, client.get(new_url) as resp2:
                    resp2.raise_for_status()
                    data2 = await resp2.json()
                    results = data2.get("results", [])
                    if results:
                        data = data2
            except Exception:
                pass
        if not results:
            if unresponsive:
                lines = [f"- {engine}: {reason}" for engine, reason in unresponsive]
                return ExecutionResult(
                    status="FAILURE",
                    message=(
                        "Web search is unavailable: every engine was blocked. "
                        "This is not 'no results'; the SearXNG instance could not "
                        "reach these engines:\n\n" + "\n".join(lines) + "\n\n"
                        "Web search requires working upstream search engines; try again "
                        "later or configure the SearXNG instance with working engines."
                    ),
                    service="web_search",
                    detail={"results": [], "source": "searxng_json", "unresponsive_engines": unresponsive},
                )
            return None

    max_results = req.max_results or 5
    results = results[:max_results]

    summary = "\n".join([f"- {r['title']}: {r['url']}" for r in results])
    return ExecutionResult(
        status="SUCCESS",
        message=f"Search results for '{req.query}':\n{summary}",
        service="web_search",
        detail={"results": results, "formatted_content": summary, "source": "searxng_json"},
    )


async def _playwright_fallback(req: WebSearchRequest) -> ExecutionResult:
    """Fallback: Playwright DOM scraping when JSON API is unavailable."""
    searxng_url = await _get_searxng_url()
    log.info(f"[browser/search] Playwright fallback for query='{req.query}'")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        search_url = f"{searxng_url}/search?q={req.query}"
        await page.goto(search_url, wait_until="networkidle")

        results = await page.evaluate("""
            () => {
                const items = Array.from(document.querySelectorAll('article.result, .result'));
                return items.slice(0, 8).map(item => {
                    const titleEl = item.querySelector('h3, h4, .title');
                    const linkEl = item.querySelector('a');
                    const snippetEl = item.querySelector('.content, .snippet');
                    return {
                        title: titleEl ? titleEl.innerText.trim() : 'No Title',
                        url: linkEl ? linkEl.href : '',
                        snippet: snippetEl ? snippetEl.innerText.trim() : ''
                    };
                });
            }
        """)

        await browser.close()

    if not results:
        return ExecutionResult(
            status="SUCCESS",
            message="Search completed but no results were found.",
            service="web_search",
            detail={"results": [], "source": "playwright_fallback"}
        )

    formatted = "\n\n".join([
        f"### {r['title']}\nURL: {r['url']}\n{r['snippet']}"
        for r in results if r['url']
    ])

    return ExecutionResult(
        status="SUCCESS",
        message=f"Found {len(results)} search results (Playwright fallback).",
        service="web_search",
        detail={"results": results, "formatted_content": formatted, "source": "playwright_fallback"}
    )

def _is_cloudflare_challenge(title: str | None, content: str) -> bool:
    """Detect a Cloudflare interstitial page (challenge or 'Just a moment' check).

    NOTE: 'challenge-platform' is deliberately NOT a marker: Cloudflare injects
    challenge-platform scripts into normal pages served via its CDN, so it is a
    false positive on real content.
    """
    lowered_title = (title or "").lower()
    lowered = content.lower()
    if "just a moment" in lowered_title:
        return True
    return any(marker in lowered for marker in (
        "cf-chl-",
        "checking your browser before accessing",
        "cloudflare-challenge",
    ))


async def _read_with_camoufox(url: str, api_key: str | None = None) -> tuple[str, str] | None:
    """Retry a web read using the camoufox anti-bot browser. Returns (title, content) or None."""
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        log.warning("[browser/read] camoufox not installed; skipping anti-bot retry")
        return None
    try:
        ctx = await AsyncCamoufox(headless=True).__aenter__()
        try:
            page = await ctx.new_page()
            if api_key:
                from urllib.parse import urlparse
                domain = urlparse(url).netloc
                if domain:
                    log.info(f"[browser/read] Injecting jarvis_api_key for {domain} (camoufox retry)")
                    await page.context.add_cookies([{
                        'name': 'jarvis_api_key',
                        'value': api_key,
                        'domain': domain.split(':')[0],
                        'path': '/'
                    }])
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = await page.title()
            content = await page.content()
            if _is_cloudflare_challenge(title, content):
                # Cloudflare's JS challenge auto-solves after a few seconds and
                # replaces the page content in place; reloading restarts it, so
                # just wait and re-read until the real page arrives.
                log.info(f"[browser/read] camoufox waiting up to 12s for challenge to solve at {url}")
                solved = False
                for _ in range(6):
                    await asyncio.sleep(2)
                    title = await page.title()
                    content = await page.content()
                    if not _is_cloudflare_challenge(title, content):
                        solved = True
                        break
                if not solved:
                    log.warning(f"[browser/read] camoufox retry still blocked by a challenge for {url}")
                    return None
            return title, content
        finally:
            await ctx.close()
    except Exception as e:
        log.warning(f"[browser/read] camoufox retry failed: {e}")
        return None


@dataclass
class FetchedDocument:
    url: str
    status: int
    content_type: str
    data: bytes
    filename: str
    too_large: bool = False


def _filename_from(url: str, disposition: str | None) -> str:
    """The file's own name: Content-Disposition first, then the URL path."""
    if disposition:
        star = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", disposition, re.I)
        plain = re.search(r'filename\s*=\s*"?([^";]+)"?', disposition, re.I)
        found = unquote(star.group(1)) if star else (plain.group(1) if plain else "")
        found = found.strip().replace("\\", "/").rsplit("/", 1)[-1]
        if found:
            return found
    name = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]).strip()
    return name or "download"


async def _fetch_document(url: str, *, cookies: dict | None = None) -> FetchedDocument:
    """GET ``url`` without a browser, stopping past WEB_READ_MAX_BYTES."""
    limit = WEB_READ_MAX_BYTES
    timeout = aiohttp.ClientTimeout(total=_FETCH_TIMEOUT_S)
    headers = {"User-Agent": _DESKTOP_UA, "Accept": "*/*"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers, cookies=cookies) as session, \
            session.get(url, allow_redirects=True) as resp:
        ctype = resp.headers.get("Content-Type", "")
        filename = _filename_from(str(resp.url), resp.headers.get("Content-Disposition"))
        declared = resp.content_length
        if declared is not None and declared > limit:
            return FetchedDocument(str(resp.url), resp.status, ctype, b"", filename, too_large=True)
        chunks: list[bytes] = []
        size = 0
        async for chunk in resp.content.iter_chunked(64 * 1024):
            size += len(chunk)
            if size > limit:
                return FetchedDocument(str(resp.url), resp.status, ctype, b"", filename, too_large=True)
            chunks.append(chunk)
        return FetchedDocument(str(resp.url), resp.status, ctype, b"".join(chunks), filename)


def _mime(content_type: str) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _charset(content_type: str) -> str:
    for part in (content_type or "").split(";")[1:]:
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            return value.strip('"\' ')
    return "utf-8"


def _is_html(doc: FetchedDocument) -> bool:
    mime = _mime(doc.content_type)
    if mime in ("text/html", "application/xhtml+xml"):
        return True
    if not mime or mime == "application/octet-stream":
        head = doc.data[:512].lstrip().lower()
        return head.startswith((b"<!doctype html", b"<html"))
    return False


def _cap(text: str) -> str:
    if len(text) > WEB_READ_MAX_CHARS:
        return text[:WEB_READ_MAX_CHARS] + "\n\n... (Content truncated due to size) ..."
    return text


def _html_to_markdown(html: str, url: str, title: str | None) -> tuple[str, str]:
    """(markdown, how): the main article when trafilatura finds one, else the whole page."""
    main = ""
    try:
        import trafilatura

        main = trafilatura.extract(
            html,
            url=url,
            include_links=False,
            include_tables=True,
            include_images=False,
            output_format="markdown",
        ) or ""
    except Exception as e:
        log.info(f"[browser/read] main-content extraction failed for {url}: {e}")
    if len(main.strip()) >= MAIN_CONTENT_MIN_CHARS:
        text, how = main.strip(), "main_content"
    else:
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.ignore_emphasis = False
        text, how = h.handle(html), "full_page"
    if title and not text.lstrip().startswith(f"# {title}"):
        text = f"# {title}\n\n{text}"
    return text, how


async def _storage_path_exists(provider, path: str) -> bool:
    try:
        from .note import _path_exists
    except ImportError:
        from handlers.note import _path_exists

    return await _path_exists(provider, path)


async def _save_download(user_context, filename: str, data: bytes, content_type: str) -> tuple[str | None, str]:
    """Store the original file under Downloads/ in the user's Nextcloud.

    Returns (path, "") or (None, why not). Never raises: a failed save must
    not cost the user the read.
    """
    try:
        from ..personal_data import missing_account_message, resolve_personal_data_provider
    except ImportError:
        from personal_data import missing_account_message, resolve_personal_data_provider
    import mimetypes

    provider = resolve_personal_data_provider(user_context)
    if provider is None:
        return None, f"The file was not saved: {missing_account_message(user_context)}"
    try:
        name = provider.sanitize_filename(filename, "download")
        stem, dot, ext = name.rpartition(".")
        if not dot or not stem:
            stem, ext = name, ""
        candidate = f"{DOWNLOADS_FOLDER}/{name}"
        number = 2
        while await _storage_path_exists(provider, candidate):
            if number > 99:
                return None, "The file was not saved: too many files with that name in Downloads."
            candidate = f"{DOWNLOADS_FOLDER}/{stem} ({number})" + (f".{ext}" if ext else "")
            number += 1
        await provider.ensure_directory(DOWNLOADS_FOLDER)
        ctype = _mime(content_type) or mimetypes.guess_type(name)[0] or "application/octet-stream"
        if not await provider.upload_file(candidate, data, ctype):
            return None, "The file was not saved: Nextcloud refused the upload."
        return candidate, ""
    except Exception as e:
        log.warning(f"[browser/read] saving {filename} failed: {e}")
        return None, f"The file was not saved: {e}"


async def _document_result(req: WebReadRequest, doc: FetchedDocument) -> ExecutionResult | None:
    """Read a fetched file. None means it is an HTML page (the browser should read it)."""
    if doc.too_large:
        limit_mb = WEB_READ_MAX_BYTES / (1024 * 1024)
        return ExecutionResult(
            status="FAILURE",
            message=f"The file at {req.url} is larger than the {limit_mb:g} MB read limit, so it was not downloaded.",
            service="web_read",
            detail={"content_type": _mime(doc.content_type), "url": doc.url},
        )
    if doc.status >= 400:
        return ExecutionResult(
            status="FAILURE",
            message=f"Web read failed: the server answered HTTP {doc.status} for {req.url}.",
            service="web_read",
        )

    try:
        from ..pdf_text import PdfReadError, extract_pdf_text, is_pdf
    except ImportError:
        from pdf_text import PdfReadError, extract_pdf_text, is_pdf

    mime = _mime(doc.content_type)
    if is_pdf(doc.content_type, doc.data):
        try:
            pdf = await asyncio.to_thread(
                extract_pdf_text,
                doc.data, start_page=req.start_page, max_chars=WEB_READ_MAX_CHARS, fallback_title=doc.filename
            )
        except PdfReadError as e:
            return ExecutionResult(status="FAILURE", message=f"Could not read the PDF at {req.url}: {e}", service="web_read")
        except ValueError as e:
            return ExecutionResult(status="FAILURE", message=str(e), service="web_read")
        detail = pdf.as_detail()
        message = f"Read PDF: {pdf.title} ({pdf.pages_label})"
        if pdf.scanned:
            message = f"Read PDF: {pdf.title} ({pdf.pages_label}). It is a scanned image with no text layer."
    elif _is_html(doc):
        return None
    elif mime.startswith("text/") or mime in ("application/json", "application/xml"):
        text = doc.data.decode(_charset(doc.content_type), errors="replace")
        detail = {"title": doc.filename, "content": _cap(text), "content_type": mime}
        message = f"Read text file: {doc.filename}"
    else:
        kind = mime or "an unknown type"
        if not req.save:
            return ExecutionResult(
                status="FAILURE",
                message=(
                    f"{req.url} is not a web page or a document with text ({kind}); it cannot be read. "
                    "Ask again with save: true to keep the file."
                ),
                service="web_read",
                detail={"content_type": mime, "url": doc.url},
            )
        detail = {"title": doc.filename, "content": "", "content_type": mime}
        message = f"{doc.filename} is {kind}, which has no text to read"

    detail["url"] = doc.url
    if req.save:
        path, why_not = await _save_download(req.user_context, doc.filename, doc.data, doc.content_type)
        if path:
            detail["saved_path"] = path
            message += f". Saved to {path}"
        else:
            detail["save_error"] = why_not
            message += f". {why_not}"
    return ExecutionResult(status="SUCCESS", message=message, service="web_read", detail=detail)


def _auth_cookies(req: WebReadRequest) -> dict | None:
    if req.use_current_user_auth and req.user_context.api_key:
        return {"jarvis_api_key": req.user_context.api_key}
    return None


async def _read_document(req: WebReadRequest) -> ExecutionResult | None:
    doc = await _fetch_document(req.url, cookies=_auth_cookies(req))
    return await _document_result(req, doc)


async def handle_web_read(req: WebReadRequest) -> ExecutionResult:
    """
    Reads a URL: a web page's main content as markdown, a PDF's text page by
    page, or a plain-text file.

    A URL ending in .pdf, and any URL whose page turns out to be a download, is
    fetched without a browser. Pages use Playwright; behind a Cloudflare
    challenge they retry with the camoufox anti-bot browser, and if that still
    fails the result says to use WebScraperRequest (camoufox by default).
    """
    log.info(f"[browser/read] url='{req.url}'")

    try:
        if urlparse(req.url).path.lower().endswith(".pdf"):
            result = await _read_document(req)
            if result is not None:
                return result

        is_download = False
        content = title = ""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()

                if req.use_current_user_auth and req.user_context.api_key:
                    domain = urlparse(req.url).netloc
                    if domain:
                        log.info(f"[browser/read] Injecting jarvis_api_key for {domain}")
                        await page.context.add_cookies([{
                            'name': 'jarvis_api_key',
                            'value': req.user_context.api_key,
                            'domain': domain.split(':')[0],
                            'path': '/'
                        }])

                try:
                    await page.goto(req.url, wait_until="domcontentloaded", timeout=30000)
                    content = await page.content()
                    title = await page.title()
                except Exception as e:
                    if "Download is starting" not in str(e):
                        raise
                    is_download = True
            finally:
                await browser.close()

        if is_download:
            log.info(f"[browser/read] {req.url} is a download; fetching it directly")
            result = await _read_document(req)
            if result is not None:
                return result
            return ExecutionResult(
                status="FAILURE",
                message=f"Web read failed: {req.url} started a download the reader could not open.",
                service="web_read",
            )

        used_anti_bot = False
        if _is_cloudflare_challenge(title, content):
            log.info(f"[browser/read] Cloudflare challenge detected for {req.url}; retrying with camoufox")
            retry = await _read_with_camoufox(req.url, req.user_context.api_key if req.use_current_user_auth else None)
            if retry is None:
                return ExecutionResult(
                    status="FAILURE",
                    message="Cloudflare challenge detected and the anti-bot retry could not bypass it. Use WebScraperRequest (camoufox engine) for this URL instead.",
                    service="web_read",
                )
            title, content = retry
            used_anti_bot = True

        markdown, how = _html_to_markdown(content, req.url, title)
        markdown = _cap(markdown)

        detail = {"title": title, "content": markdown, "extraction": how}
        notes = []
        if used_anti_bot:
            notes.append("Retrieved via camoufox anti-bot browser after a Cloudflare challenge")
        if req.save:
            notes.append("save applies to downloaded files (PDFs and other documents), not web pages; nothing was saved")
        if notes:
            detail["note"] = ". ".join(notes)
        return ExecutionResult(
            status="SUCCESS",
            message=f"Successfully read page: {title}",
            service="web_read",
            detail=detail,
        )

    except Exception as e:
        log.error(f"Web read failed: {e}")
        return ExecutionResult(status="FAILURE", message=f"Web read failed: {e!s}", service="web_read")
