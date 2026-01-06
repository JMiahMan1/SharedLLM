import asyncio
import re
import sys
import os
import logging

# Add project root to path
sys.path.append(os.getcwd())

from app.logic.web_search import tool_web_search

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

async def main():
    print("--- Testing Web Search Integrations (Regex Compatibility) ---")
    
    # Query likely to return YouTube results
    query = "site:youtube.com OpenAI Sora"
    
    print(f"\nPerforming Search for: '{query}'")
    try:
        result_text = await tool_web_search(query)
    except Exception as e:
        print(f"FATAL: Search Tool Failed: {e}")
        return

    print(f"\n--- Search Result Output Sample (First 500 chars) ---")
    print(result_text[:500] + "...")
    print("-----------------------------------------------------")

    # TEST 1: standard.py Regex
    # url_pattern = r'URL:\s*(https?://[^\s\n]+)'
    print("\n[TEST 1] standard.py Integration (Generic URL)")
    pattern_std = r'URL:\s*(https?://[^\s\n]+)'
    urls_std = re.findall(pattern_std, result_text)
    print(f"Found URLs: {urls_std}")
    if urls_std:
        print(">> PASS: standard.py regex found URLs.")
    else:
        print(">> FAIL: standard.py regex found NOTHING.")

    # TEST 2: media_ops.py Regex
    # r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=[^"\s]+|youtu\.be/[^"\s]+))'
    print("\n[TEST 2] media_ops.py Integration (YouTube specific)")
    pattern_ops = r'(https?://(?:www\.)?(?:youtube\.com/watch\?v=[^"\s]+|youtu\.be/[^"\s]+))'
    match_ops = re.search(pattern_ops, result_text)
    if match_ops:
        print(f">> PASS: media_ops.py regex found: {match_ops.group(1)}")
    else:
        print(">> FAIL: media_ops.py regex found NOTHING.")

    # TEST 3: android_tv_ops.py Regex
    # r'(https?://www\.youtube\.com/watch\?v=[\w-]+)'
    print("\n[TEST 3] android_tv_ops.py Integration (YouTube Strict)")
    pattern_atv = r'(https?://www\.youtube\.com/watch\?v=[\w-]+)'
    match_atv = re.search(pattern_atv, result_text)
    if match_atv:
        print(f">> PASS: android_tv_ops.py regex found: {match_atv.group(1)}")
    else:
        print(">> FAIL: android_tv_ops.py regex found NOTHING.")

if __name__ == "__main__":
    asyncio.run(main())
