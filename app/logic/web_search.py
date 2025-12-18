# app/logic/web_search.py
import re
import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from app.settings import log, run_blocking, WHOOGLE_URL, SEARCH_HEADERS

# Try importing Playwright for Tier 3
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

async def tool_web_search(query: str) -> str:
    """
    Executes a web search using a 4-Tier Fallback strategy:
    1. Whoogle JSON API (Fastest) -> Populates results
    2. Whoogle HTML Scraping (Fast) -> Populates results if empty
    3. DuckDuckGo (Fallback) -> Populates results if empty
    4. Playwright Headless Browser (Slowest, most robust) -> Populates results if empty
    """
    if not WHOOGLE_URL: return ""
    
    # Fallback Cleaning: If query looks like JSON (e.g. {"title":...}), clean it
    if query.strip().startswith("{") and "}" in query:
        match = re.search(r':\s*"([^"]+)"', query)
        if match: query = match.group(1)
    
    log.info(f"Executing Web Search for: {query}")
    
    parsed = urlparse(WHOOGLE_URL)
    search_endpoint = WHOOGLE_URL if "search" in parsed.path else f"{WHOOGLE_URL.rstrip('/')}/search"

    results: list[dict] = []

    # --- Tier 1: JSON API ---
    try:
        def do_search_json():
            return requests.get(search_endpoint, params={"q": query, "format": "json"}, headers=SEARCH_HEADERS, timeout=6)
        
        r = await run_blocking(do_search_json)
        if r.status_code == 200:
            data = r.json()
            raw_results = data.get("results", data.get("hits", []))
            if raw_results:
                for res in raw_results[:5]:
                    url = res.get('url') or res.get('link') or res.get('href')
                    if url:
                        results.append({
                            "title": res.get('title'),
                            "url": url,
                            "snippet": res.get('content', '')
                        })
                
                if not results:
                     log.warning("Web Search Tier 1 (JSON) returned data but no valid URLs.")
        else:
            log.warning(f"Web Search Tier 1 (JSON) returned status {r.status_code}")
    except Exception as e:
        log.warning(f"Web Search Tier 1 (JSON) Failed: {e}. Switching to Tier 2.")


    # --- Tier 2: HTML Scraping ---
    if not results:
        try:
            def do_search_html():
                return requests.get(search_endpoint, params={"q": query}, headers=SEARCH_HEADERS, timeout=8)
            
            r = await run_blocking(do_search_html)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                # Standard Whoogle/Google result selectors
                selectors = [".result", "#main .result", ".result-content", "article", ".g", "div[class*='result']"]
                
                found_nodes = []
                for sel in selectors:
                    found_nodes = soup.select(sel)
                    if found_nodes: break
                
                for res in found_nodes[:5]:
                    title = res.select_one("h3, a, h2")
                    body = res.select_one(".content, .st, p")
                    if title and body:
                        t_text = title.get_text(strip=True)
                        b_text = body.get_text(strip=True)
                        link = title.get("href") or ""
                        if t_text and b_text:
                            results.append({
                                "title": t_text,
                                "url": link,
                                "snippet": b_text,
                                "source": "Whoogle HTML"
                            })
        except Exception as e:
            log.warning(f"Web Search Tier 2 (HTML) Error: {e}")

    # --- Tier 3: DuckDuckGo (Fallback) ---
    if not results:
        try:
            from duckduckgo_search import DDGS
            def do_ddg_search():
                log.info("Engaging Tier 3 (DuckDuckGo) for web search...")
                with DDGS() as ddgs:
                    return list(ddgs.text(query, max_results=5))
            
            ddg_results = await run_blocking(do_ddg_search)
            if ddg_results:
                for res in ddg_results:
                    results.append({
                        "title": res.get('title'),
                        "url": res.get('href'),
                        "snippet": res.get('body'),
                        "source": "DuckDuckGo"
                    })
        except ImportError:
            log.warning("DuckDuckGo Search library not installed.")
        except Exception as e:
            log.warning(f"Web Search Tier 3 (DuckDuckGo) Error: {e}")

    # --- Tier 4: Playwright ---
    if not results and PLAYWRIGHT_AVAILABLE:
        log.info("Engaging Tier 4 (Playwright) for web search...")
        browser_url = f"{WHOOGLE_URL.rstrip('/')}/search?q={query}"
        # Now returns List[Dict] directly
        playwright_results = await _scrape_with_playwright(browser_url)
        if playwright_results:
            results.extend(playwright_results)

    if results:
        return _format_results(results)

    return "System Notification: Web search performed but returned no results or failed."

def _format_results(results: list[dict]) -> str:
    """Standardizes list of result, dicts into the Markdown format."""
    formatted = []
    source_label = results[0].get("source", "Web Search")
    
    for res in results:
        t = res.get("title", "No Title")
        u = res.get("url", "#")
        s = res.get("snippet", "No Content")
        formatted.append(f"Title: {t}\nURL: {u}\nSnippet: {s}")
    
    return f"### Real-time Web Search Results ({source_label}):\n" + "\n\n".join(formatted)


async def _scrape_with_playwright(url) -> list[dict]:
    """
    Scrapes a URL using a headless browser. Returns LIST of DICTS.
    Includes Docker-compatible launch arguments and Robust Fallback Logic.
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
                # --- Primary Navigation (Robust) ---
                try:
                    # Wait for network idle to ensure dynamic content loads
                    await page.goto(url, timeout=15000, wait_until="networkidle")
                    content = await page.content()
                    log.info(f"Playwright fetched page content length: {len(content)}")
                    
                    soup = BeautifulSoup(content, "html.parser")
                    selectors = [".result", "#main .result", ".result-content", "article", ".g"]
                    
                    found_nodes = []
                    for sel in selectors:
                        found_nodes = soup.select(sel)
                        if found_nodes: break
                    
                    for res in found_nodes[:5]:
                        title = res.select_one("h3, a, h2")
                        body = res.select_one(".content, .st, p, .result-body")
                        if title and body:
                            results.append({
                                "title": title.get_text(strip=True),
                                "url": title.get("href") or "",
                                "snippet": body.get_text(strip=True),
                                "source": "Playwright Whoogle"
                            })
                except Exception as pg_err:
                     log.warning(f"Playwright Primary URL Failed: {pg_err}. Proceeding to Fallback.")

                # --- Fallback Logic ---
                if not results:
                     log.warning("Playwright: Whoogle selectors failed or URL down. Trying DuckDuckGo HTML fallback...")
                     
                     try:
                         # Extract query from URL safely
                         query_part = url.split('q=')[-1].split('&')[0]
                     except Exception:
                         query_part = "unknown"

                     ddg_url = f"https://html.duckduckgo.com/html/?q={query_part}"
                     
                     try:
                         await page.goto(ddg_url, timeout=15000, wait_until="networkidle")
                         content = await page.content()
                         soup = BeautifulSoup(content, "html.parser")
                         
                         ddg_selectors = [".result", ".web-result"]
                         found_nodes = []
                         for sel in ddg_selectors:
                             found_nodes = soup.select(sel)
                             if found_nodes: break
                             
                         for res in found_nodes[:5]:
                             title = res.select_one(".result__title, .result__a")
                             body = res.select_one(".result__snippet, .result__snippet")
                             if title:
                                 t_text = title.get_text(strip=True)
                                 link_tag = title.find("a")
                                 link = link_tag.get("href") if link_tag else title.get("href")
                                 b_text = body.get_text(strip=True) if body else ""
                                 
                                 if t_text and link:
                                     results.append({
                                         "title": t_text,
                                         "url": link,
                                         "snippet": b_text,
                                         "source": "Playwright DDG"
                                     })

                     except Exception as ddg_err:
                         log.warning(f"Playwright DDG Fallback Failed: {ddg_err}")

                # Double Fallback: Try Bing
                if not results:
                     log.warning("Playwright: DDG failed. Trying Bing HTML fallback...")
                     try:
                         query_part = url.split('q=')[-1].split('&')[0]
                     except Exception:
                         query_part = "unknown"

                     bing_url = f"https://www.bing.com/search?q={query_part}"
                     
                     try:
                         await page.goto(bing_url, timeout=15000, wait_until="networkidle")
                         content = await page.content()
                         soup = BeautifulSoup(content, "html.parser")
                         
                         bing_results = soup.select(".b_algo")
                         for res in bing_results[:5]:
                             title = res.select_one("h2 a")
                             body = res.select_one(".b_caption p, .b_algoSlug")
                             if title:
                                  t_text = title.get_text(strip=True)
                                  link = title.get("href")
                                  b_text = body.get_text(strip=True) if body else ""
                                  if link:
                                      results.append({
                                          "title": t_text,
                                          "url": link,
                                          "snippet": b_text,
                                          "source": "Playwright Bing"
                                      })
                     except Exception as bing_err:
                         log.warning(f"Playwright Bing Fallback Failed: {bing_err}")

                if not results:
                     log.info("Playwright: All selectors failed, grabbing generalized body text.")
                     if soup and soup.body:
                         text = soup.body.get_text(separator=' ', strip=True)[:2000]
                         results.append({
                             "title": "Page Text Dump",
                             "url": url,
                             "snippet": text,
                             "source": "Playwright Text Dump"
                         })
                     
            except Exception as inner_e:
                log.warning(f"Playwright Page Logic Error: {inner_e}")
            finally:
                await browser.close()
                
    except Exception as e:
        log.error(f"Playwright Engine Error: {e}")
        
    return results
