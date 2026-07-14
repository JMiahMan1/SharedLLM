# services/execution/handlers/browser.py
import logging
import os
import sys
import time
from urllib.parse import urlencode

import aiohttp
import html2text
from playwright.async_api import async_playwright

try:
    from schemas import ExecutionResult, WebReadRequest, WebSearchRequest
except ImportError:
    from ..schemas import ExecutionResult, WebReadRequest, WebSearchRequest

log = logging.getLogger("execution.browser")

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
        from main import IDENTITY_SVC_URL, INTERNAL_SECRET
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

async def handle_web_search(req: WebSearchRequest) -> ExecutionResult:
    """
    Performs a web search via the SearXNG JSON API (fast, structured path),
    with a Playwright DOM fallback for the rare case the JSON endpoint is
    unavailable. The instance is configured via the `searxng_url` Identity
    setting (the search instance's base URL).
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
    """Primary path: SearXNG JSON API. Fast, structured, no HTML scraping."""
    searxng_url = await _get_searxng_url()
    params = {
        "q": req.query,
        "format": "json",
        "categories": req.category or DEFAULT_CATEGORY,
        "language": req.language or DEFAULT_LANGUAGE,
        "safesearch": req.safesearch if req.safesearch is not None else DEFAULT_SAFESAFERCH,
        "pageno": req.pageno or 1,
    }
    if req.engines:
        params["engines"] = req.engines
    if req.time_range:
        params["time_range"] = req.time_range

    url = f"{searxng_url}/search?{urlencode(params)}"
    log.info(f"[browser/search] SearXNG JSON search: {url}")

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12.0)) as client, client.get(url) as resp:
            resp.raise_for_status()
            data = await resp.json()
    except Exception as e:
        # Surface to the Playwright fallback — the JSON endpoint being down or
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

async def handle_web_read(req: WebReadRequest) -> ExecutionResult:
    """
    Fetches a URL and converts it to markdown.
    """
    log.info(f"[browser/read] url='{req.url}'")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            if req.use_current_user_auth and req.user_context.api_key:
                from urllib.parse import urlparse
                domain = urlparse(req.url).netloc
                if domain:
                    log.info(f"[browser/read] Injecting jarvis_api_key for {domain}")
                    await page.context.add_cookies([{
                        'name': 'jarvis_api_key',
                        'value': req.user_context.api_key,
                        'domain': domain.split(':')[0],
                        'path': '/'
                    }])

            # Standard timeout and wait condition
            await page.goto(req.url, wait_until="domcontentloaded", timeout=30000)

            # Get the page content
            content = await page.content()
            title = await page.title()

            await browser.close()

            # Convert to markdown
            h = html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = True
            h.ignore_emphasis = False
            markdown = h.handle(content)

            # Truncate if too large for LLM context (e.g., 15k chars)
            if len(markdown) > 15000:
                markdown = markdown[:15000] + "\n\n... (Content truncated due to size) ..."

            return ExecutionResult(
                status="SUCCESS",
                message=f"Successfully read page: {title}",
                service="web_read",
                detail={"title": title, "content": markdown}
            )

    except Exception as e:
        log.error(f"Web read failed: {e}")
        return ExecutionResult(status="FAILURE", message=f"Web read failed: {e!s}", service="web_read")
