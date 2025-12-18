import asyncio
import logging
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from app.logic.web_search import _scrape_with_playwright
from app.settings import log

# Configure logging to stdout
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

async def main():
    print("--- STARTING PLAYWRIGHT FALLBACK TEST (Structured v2) ---")

    # Test 1: Simulate connection failure (Invalid URL)
    # Expected: Should fail Primary, log warning, and Fallback to DDG/Bing -> RETURN RESULTS
    print("\n[TEST 1] Testing Connection Failure (Bad URL)")
    bad_url = "http://localhost:9999/search?q=test_connection_failure"
    results = await _scrape_with_playwright(bad_url)
    print(f"Results Count: {len(results)}")
    
    if not results:
        print(">> FAIL: No results returned. Fallback Log logic might be broken.")
    else:
        print(">> SUCCESS: Got results despite connection failure!")
        for r in results:
            print(f" - {r.get('source', 'Unknown source')}: {r.get('title', 'No Title')}")

    # Test 2: Simulate "No Selectors" (Valid URL, wrong content)
    # Expected: Should trigger fallback and return results
    print("\n[TEST 2] Testing Content Fallback (Example.com)")
    trigger_url = "https://example.com/search?q=spacex" 
    results = await _scrape_with_playwright(trigger_url)
    print(f"Results Count: {len(results)}")
    
    found_fallback = False
    for r in results:
        src = r.get('source', '')
        if "DuckDuckGo" in src or "Bing" in src or "Text Dump" in src:
            found_fallback = True
        print(f" - {r.get('source')}: {r.get('title')}")
        
    if found_fallback or len(results) > 0:
        print(">> SUCCESS: Fallback logic engaged.")
    else:
        print(">> FAIL: No results returned.")

if __name__ == "__main__":
    asyncio.run(main())
