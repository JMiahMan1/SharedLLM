# services/execution/handlers/browser.py
import logging
import asyncio
import html2text
from playwright.async_api import async_playwright
from typing import Optional

try:
    from schemas import ExecutionResult, WebSearchRequest, WebReadRequest
except ImportError:
    from schemas import ExecutionResult, WebSearchRequest, WebReadRequest

log = logging.getLogger("execution.browser")

SEARCH_BASE_URL = "https://search.sumemail.com/search"

async def handle_web_search(req: WebSearchRequest) -> ExecutionResult:
    """
    Performs a web search using the user's SearXNG instance.
    """
    log.info(f"[browser/search] query='{req.query}'")
    
    try:
        async with async_playwright() as p:
            # We use a light headless browser as requested.
            # We try to use the host's Brave if available, but for container portability,
            # we'll default to the installed chromium in the container.
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # Construct the SearXNG search URL
            # Note: format=json might be blocked or require settings, so we'll just scrape the results
            search_url = f"{SEARCH_BASE_URL}?q={req.query}"
            await page.goto(search_url, wait_until="networkidle")
            
            # Extract result titles, snippets, and URLs
            # SearXNG results are usually in <article class="result"> or similar
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
                    detail={"results": []}
                )
            
            # Format results as a readable list for the LLM
            formatted_results = "\n\n".join([
                f"### {r['title']}\nURL: {r['url']}\n{r['snippet']}"
                for r in results if r['url']
            ])
            
            return ExecutionResult(
                status="SUCCESS",
                message=f"Found {len(results)} search results.",
                service="web_search",
                detail={"results": results, "formatted_content": formatted_results}
            )
            
    except Exception as e:
        log.error(f"Web search failed: {e}")
        return ExecutionResult(status="FAILURE", message=f"Web search failed: {str(e)}", service="web_search")

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
