# services/execution/handlers/browser.py
import logging
import os
import html2text
import httpx
from urllib.parse import urlencode
from playwright.async_api import async_playwright
from typing import Optional

try:
    from schemas import ExecutionResult, WebSearchRequest, WebReadRequest
except ImportError:
    from schemas import ExecutionResult, WebSearchRequest, WebReadRequest

log = logging.getLogger("execution.browser")

SEARXNG_URL = os.getenv("SEARXNG_URL", os.getenv("WHOOGLE_URL", "https://search.sumemail.com/")).rstrip("/")

DEFAULT_ENGINES = "google,bing,duckduckgo"
DEFAULT_LANGUAGE = "en"
DEFAULT_CATEGORY = "general"
DEFAULT_SAFESAFERCH = 0

async def handle_web_search(req: WebSearchRequest) -> ExecutionResult:
    """
    Performs a web search via SearXNG JSON API with Playwright fallback.
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
        return ExecutionResult(status="FAILURE", message=f"Web search failed: {str(e)}", service="web_search")


async def _searxng_json_search(req: WebSearchRequest) -> Optional[ExecutionResult]:
    """Primary path: SearXNG native JSON API."""
    params = {
        "q": req.query,
        "format": "json",
        "categories": req.category or DEFAULT_CATEGORY,
        "language": req.language or DEFAULT_LANGUAGE,
        "safesearch": req.safesearch if req.safesearch is not None else DEFAULT_SAFESAFERCH,
    }
    if req.engines:
        params["engines"] = req.engines
    else:
        params["engines"] = DEFAULT_ENGINES
    if req.time_range:
        params["time_range"] = req.time_range
    if req.pageno:
        params["pageno"] = req.pageno

    search_url = f"{SEARXNG_URL}/search?{urlencode(params)}"
    log.info(f"[browser/search] SearXNG JSON API: {search_url}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(search_url)
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    if not results:
        return ExecutionResult(
            status="SUCCESS",
            message="Search completed but no results were found.",
            service="web_search",
            detail={"results": [], "source": "searxng_json"}
        )

    structured = []
    for r in results[:10]:
        structured.append({
            "title": r.get("title", "No Title"),
            "url": r.get("url", ""),
            "snippet": r.get("content", ""),
            "engine": r.get("engine", "unknown"),
            "score": r.get("score", 0),
        })

    formatted = "\n\n".join([
        f"### {r['title']}\nURL: {r['url']}\n{r['snippet']}\nSource: {r['engine']}"
        for r in structured if r['url']
    ])

    return ExecutionResult(
        status="SUCCESS",
        message=f"Found {len(structured)} search results via SearXNG JSON API.",
        service="web_search",
        detail={
            "results": structured,
            "formatted_content": formatted,
            "source": "searxng_json",
            "total_results": data.get("number_of_results", len(results)),
        }
    )


async def _playwright_fallback(req: WebSearchRequest) -> ExecutionResult:
    """Fallback: Playwright DOM scraping when JSON API is unavailable."""
    log.info(f"[browser/search] Playwright fallback for query='{req.query}'")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        search_url = f"{SEARXNG_URL}/search?q={req.query}"
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
        return ExecutionResult(status="FAILURE", message=f"Web read failed: {str(e)}", service="web_read")
