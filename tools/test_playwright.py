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
    print("--- STARTING PLAYWRIGHT FALLBACK TEST ---")

    # Test 1: Simulate connection failure (Invalid URL)
    # Expected: Should fail and return empty (skipping fallbacks) due to Logic Flaw?
    print("\n[TEST 1] Testing Connection Failure (Bad URL)")
    bad_url = "http://localhost:9999/search?q=test_connection_failure"
    results = await _scrape_with_playwright(bad_url)
    print(f"Results Count: {len(results)}")
    if not results:
        print(">> FAIL (Expected behavior if flaw exists): No results returned from connection failure.")
    else:
        print(">> SUCCESS: Got results (Fallback worked?)")
        for r in results: print(r[:100] + "...")

    # Test 2: Simulate "No Selectors" (Valid URL, wrong content)
    # Expected: Should trigger fallback to DuckDuckGo/Bing
    print("\n[TEST 2] Testing Content Fallback (Example.com)")
    # Using example.com which won't match '.result' selectors
    trigger_url = "https://example.com/search?q=spacex" 
    results = await _scrape_with_playwright(trigger_url)
    print(f"Results Count: {len(results)}")
    
    found_fallback = False
    for r in results:
        if "DuckDuckGo" in r or "Bing" in r or "URL" in r:
            found_fallback = True
        print(r[:100] + "...")
        
    if found_fallback or len(results) > 0:
        print(">> SUCCESS: Fallback logic engaged (returned results from DDG/Bing or Text body).")
    else:
        print(">> FAIL: No results returned even after fallback attempt.")

if __name__ == "__main__":
    asyncio.run(main())
