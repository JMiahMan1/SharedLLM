# app/logic/web_search.py
import re
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from settings import log, run_blocking, WHOOGLE_URL, SEARCH_HEADERS

# Try importing Playwright for Tier 3
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

async def tool_web_search(query: str) -> str:
    """
    Executes a web search using a 3-Tier Fallback strategy:
    1. Whoogle JSON API (Fastest)
    2. Whoogle HTML Scraping (Fast)
    3. Playwright Headless Browser (Slowest, most robust)
    """
    if not WHOOGLE_URL: return ""
    
    # Fallback Cleaning: If query looks like JSON (e.g. {"title":...}), clean it
    if query.strip().startswith("{") and "}" in query:
        match = re.search(r':\s*"([^"]+)"', query)
        if match: query = match.group(1)
    
    log.info(f"Executing Web Search for: {query}")
    
    parsed = urlparse(WHOOGLE_URL)
    search_endpoint = WHOOGLE_URL if "search" in parsed.path else f"{WHOOGLE_URL.rstrip('/')}/search"

    # --- Tier 1: JSON API ---
    try:
        def do_search_json():
            return requests.get(search_endpoint, params={"q": query, "format": "json"}, headers=SEARCH_HEADERS, timeout=6)
        
        r = await run_blocking(do_search_json)
        if r.status_code == 200:
            try:
                data = r.json()
                results = data.get("results", data.get("hits", []))
                if results:
                    # Validate that we actually have URLs
                    has_urls = any(r.get('url') or r.get('link') or r.get('href') for r in results)
                    if not has_urls:
                        log.warning("Web Search Tier 1 (JSON) returned results but NO URLs. Falling back to Tier 2.")
                        # Raise exception or pass to trigger fallback? 
                        # 'pass' allows exiting the try/except block naturally? No, we are in a function. 
                        # We need to NOT return here.
                        raise ValueError("No URLs in JSON response")

                    formatted = [f"Title: {res.get('title')}\nURL: {res.get('url', res.get('link', res.get('href', '')))}\nSnippet: {res.get('content', '')}" for res in results[:4]]
                    return "### Real-time Web Search Results (JSON):\n" + "\n\n".join(formatted)
            except Exception as e:
                log.warning(f"Web Search Tier 1 (JSON) Processing Error: {e}. Switching to Tier 2.") 
    except requests.exceptions.ConnectionError:
        log.error(f"Whoogle (Tier 1) Unreachable at {WHOOGLE_URL}. Service might be down. Switching to Tier 2.")
    except Exception as e:
        log.warning(f"Web Search Tier 1 (JSON) Failed: {e}. Switching to Tier 2.")

    # --- Tier 2: HTML Scraping ---
    # Useful if the JSON endpoint is disabled or blocked, but the frontend works.
    try:
        def do_search_html():
            return requests.get(search_endpoint, params={"q": query}, headers=SEARCH_HEADERS, timeout=8)
        
        r = await run_blocking(do_search_html)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            results = []
            # Standard Whoogle/Google result selectors
            selectors = [".result", "#main .result", ".result-content", "article", ".g", "div[class*='result']"]
            
            for sel in selectors:
                found = soup.select(sel)
                if found:
                    for res in found[:4]:
                        title = res.select_one("h3, a, h2")
                        body = res.select_one(".content, .st, p")
                        if title and body:
                            t_text = title.get_text(strip=True)
                            b_text = body.get_text(strip=True)
                            if t_text and b_text:
                                link = title.get("href") or ""
                                results.append(f"Title: {t_text}\nURL: {link}\nSnippet: {b_text}")
                    if len(results) >= 2: break
            
            if results:
                return "### Real-time Web Search Results (HTML):\n" + "\n\n".join(results)
    except requests.exceptions.ConnectionError:
        log.error(f"Whoogle (Tier 2) Unreachable. Entire Whoogle instance appears DOWN. Switching to DuckDuckGo/Playwright.")
    except Exception as e:
        log.warning(f"Web Search Tier 2 (HTML) Error: {e}. Switching to Fallback Provider.")

    # --- Tier 3: DuckDuckGo (Fallback) ---
    # Public, reliable, no self-hosting required.
    try:
        from duckduckgo_search import DDGS
        
        def do_ddg_search():
            log.info("Engaging Tier 3 (DuckDuckGo) for web search...")
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=4))
        
        ddg_results = await run_blocking(do_ddg_search)
        if ddg_results:
            formatted = []
            for res in ddg_results:
                title = res.get('title')
                link = res.get('href')
                snippet = res.get('body')
                if title and link:
                    formatted.append(f"Title: {title}\nURL: {link}\nSnippet: {snippet}")
            
            if formatted:
                return "### Real-time Web Search Results (DuckDuckGo):\n" + "\n\n".join(formatted)
    except ImportError:
        log.warning("DuckDuckGo Search library not installed.")
    except Exception as e:
        log.warning(f"Web Search Tier 3 (DuckDuckGo) Error: {e}")

    # --- Tier 4: Playwright ---
    # Heavyweight browser automation for JS-heavy results or CAPTCHA avoidance.
    if PLAYWRIGHT_AVAILABLE:
        log.info("Engaging Tier 4 (Playwright) for web search...")
        browser_url = f"{WHOOGLE_URL.rstrip('/')}/search?q={query}"
        results = await _scrape_with_playwright(browser_url)
        if results:
             return "### Real-time Web Search Results (Playwright):\n" + "\n\n".join(results)

    return "System Notification: Web search performed but returned no results or failed."

async def _scrape_with_playwright(url):
    """
    Scrapes a URL using a headless browser. 
    Includes Docker-compatible launch arguments.
    """
    if not PLAYWRIGHT_AVAILABLE: 
        log.warning("Playwright is not available/installed.")
        return []
        
    results = []
    try:
        log.info(f"Launching Playwright for URL: {url}")
        async with async_playwright() as p:
            # CRITICAL: Docker-friendly args to prevent crashes
            browser = await p.chromium.launch(
                headless=True, 
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            context = await browser.new_context(user_agent=SEARCH_HEADERS["User-Agent"])
            page = await context.new_page()
            
            try:
                # Wait for network idle to ensure dynamic content loads
                await page.goto(url, timeout=15000, wait_until="networkidle")
                content = await page.content()
                log.info(f"Playwright fetched page content length: {len(content)}")
                
                soup = BeautifulSoup(content, "html.parser")
                selectors = [".result", "#main .result", ".result-content", "article", ".g"]
                
                for sel in selectors:
                    found = soup.select(sel)
                    if found:
                        for res in found[:4]:
                            title = res.select_one("h3, a, h2")
                            body = res.select_one(".content, .st, p, .result-body")
                            if title and body:
                                link = title.get("href") or ""
                                results.append(f"Title: {title.get_text(strip=True)}\nURL: {link}\nSnippet: {body.get_text(strip=True)}")
                        if len(results) >= 2: break
                
                # Fallback: If no structured results found, grab the body text
                # Fallback: If no structured results found, grab the body text
                # But first, try DuckDuckGo HTML if Whoogle failed (heuristic check)
                if not results:
                     log.warning("Playwright: Whoogle selectors failed. Trying DuckDuckGo HTML fallback...")
                     ddg_url = f"https://html.duckduckgo.com/html/?q={url.split('q=')[-1]}"
                     await page.goto(ddg_url, timeout=15000, wait_until="networkidle")
                     content = await page.content()
                     soup = BeautifulSoup(content, "html.parser")
                     
                     
                     # DDG HTML Selectors
                     ddg_selectors = [".result", ".web-result"]
                     for sel in ddg_selectors:
                         found = soup.select(sel)
                         if found:
                             for res in found[:4]:
                                 title = res.select_one(".result__title, .result__a")
                                 body = res.select_one(".result__snippet, .result__snippet")
                                 if title:
                                     t_text = title.get_text(strip=True)
                                     link = title.find("a").get("href") if title.find("a") else None
                                     if not link and title.name == 'a': link = title.get("href")
                                     
                                     b_text = body.get_text(strip=True) if body else ""
                                     if t_text and link:
                                         results.append(f"Title: {t_text}\nURL: {link}\nSnippet: {b_text}")
                             if len(results) >= 2: break
                
                # Double Fallback: Try Bing if DDG failed/blocked
                if not results:
                     log.warning("Playwright: DDG failed. Trying Bing HTML fallback...")
                     bing_url = f"https://www.bing.com/search?q={url.split('q=')[-1]}"
                     await page.goto(bing_url, timeout=15000, wait_until="networkidle")
                     content = await page.content()
                     soup = BeautifulSoup(content, "html.parser")
                     
                     bing_results = soup.select(".b_algo")
                     for res in bing_results[:4]:
                         title = res.select_one("h2 a")
                         body = res.select_one(".b_caption p, .b_algoSlug")
                         if title:
                              t_text = title.get_text(strip=True)
                              link = title.get("href")
                              b_text = body.get_text(strip=True) if body else ""
                              if link:
                                  results.append(f"Title: {t_text}\nURL: {link}\nSnippet: {b_text}")

                if not results:
                     log.info("Playwright: All selectors failed, grabbing generalized body text.")
                     text = soup.body.get_text(separator=' ', strip=True)[:2000]
                     results.append(f"Page Text: {text}")
                     
            except Exception as e:
                log.warning(f"Playwright Page Error: {e}")
            finally:
                await browser.close()
                
    except Exception as e:
        log.error(f"Playwright Engine Error: {e}")
        
    return results
