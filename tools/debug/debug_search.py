import os
import subprocess
import sys
import warnings


# --- 0. Dependency Auto-Heal ---
def check_and_install(package, import_name=None):
    import_name = import_name or package
    try:
        __import__(import_name)
    except ImportError:
        print(f"[*] Dependency '{package}' missing. Installing...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
            print(f"[*] '{package}' installed successfully.")
        except Exception as e:
            print(f"[!] Failed to install '{package}'. Test cannot proceed. Error: {e}")
            sys.exit(1)

check_and_install("requests")
check_and_install("python-dotenv", "dotenv")
check_and_install("beautifulsoup4", "bs4")

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from urllib3.exceptions import InsecureRequestWarning

# Suppress SSL warnings for local/self-hosted instances
warnings.simplefilter('ignore', InsecureRequestWarning)

# --- 1. Configuration & Environment Loading ---
# Get the absolute path of THIS script (SharedLLM/test/debug_search.py)
script_path = os.path.abspath(__file__)
test_dir = os.path.dirname(script_path)       # .../SharedLLM/test
project_root = os.path.dirname(test_dir)      # .../SharedLLM (The Root)
env_path = os.path.join(project_root, '.env')

print(f"\n{'='*60}")
print("CONFIG DIAGNOSTIC")
print(f"{'='*60}")
print(f"Script Location: {script_path}")
print(f"Project Root:    {project_root}")
print(f"Looking for .env at: {env_path}")

if os.path.exists(env_path):
    print("   [OK] .env file found.")
    load_dotenv(env_path)
else:
    print("   [FAIL] .env file NOT found at this location.")
    print("   Please ensure you are running this from the project root.")

# Load Variable
WHOOGLE_URL = os.getenv("WHOOGLE_URL")
QUERY = "Linux kernel version"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"}

def print_sep(title):
    print(f"\n{'='*60}\n{title}\n{'='*60}")

# --- 2. Diagnostic Tests ---
def test_json():
    print_sep("TEST 1: JSON API Mode (Preferred)")

    if not WHOOGLE_URL:
        print("[!] CRITICAL: WHOOGLE_URL is not set in .env")
        return False

    # Clean URL
    target = f"{WHOOGLE_URL.rstrip('/')}/search?q={QUERY}&format=json"
    print(f"Target URL: {target}")

    try:
        r = requests.get(target, headers=HEADERS, timeout=10, verify=False)
        print(f"HTTP Status: {r.status_code}")

        if r.status_code != 200:
            print(f"FAIL: Non-200 Status. Response Preview:\n{r.text[:300]}")
            return False

        try:
            data = r.json()
            results = data.get("results", [])
            print(f"JSON Keys: {list(data.keys())}")
            print(f"Results Found: {len(results)}")

            if results:
                print(f"\n[SUCCESS] Sample Result 1:\n   Title: {results[0].get('title')}\n   Content: {str(results[0].get('content', ''))[:100]}...")
                return True
            else:
                print("[FAIL] Valid JSON received, but 'results' list is empty.")
                return False
        except Exception as e:
            print(f"[FAIL] Response was not valid JSON (likely HTML error page).\nError: {e}")
            # print(f"Raw Preview: {r.text[:200]}")
            return False

    except Exception as e:
        print(f"[CRITICAL] Connection failed: {e}")
        return False

def test_html():
    print_sep("TEST 2: HTML Scraping Mode (Fallback)")

    if not WHOOGLE_URL: return

    target = f"{WHOOGLE_URL.rstrip('/')}/search?q={QUERY}"
    print(f"Target URL: {target}")

    try:
        r = requests.get(target, headers=HEADERS, timeout=10, verify=False)
        print(f"HTTP Status: {r.status_code}")

        soup = BeautifulSoup(r.text, "html.parser")

        # A. Check Selectors
        selectors = [
            ".result",
            "#main .result",
            ".result-content",
            ".g",
            ".result-default",
            "div[class*='result']",
            "article",
            "#urls article",
            ".result-body"
        ]

        found_selector = False
        print("\n--- Checking CSS Selectors ---")
        for sel in selectors:
            count = len(soup.select(sel))
            print(f"   '{sel}': {count} matches")
            if count > 0: found_selector = True

        # B. Check Text Density (The Last Resort)
        print("\n--- Checking Text Density (Brute Force) ---")
        paragraphs = [p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 60]
        print(f"   Found {len(paragraphs)} text-heavy paragraphs (>60 chars).")

        if found_selector:
            print("\n>>> DIAGNOSIS: HTML Structure is standard. Logic.py Tier 2 will work.")
        elif paragraphs:
            print("\n>>> DIAGNOSIS: HTML Structure is non-standard/obfuscated.")
            print(">>> HOWEVER: Logic.py Tier 3 (Text Density) WILL work using paragraphs.")
            print(f"Sample Text: {paragraphs[0][:100]}...")
        else:
            print("\n[FAIL] No results found via Selectors OR Text Density.")
            print("Dumping HTML structure (First 500 chars):")
            print(soup.prettify()[:500])

    except Exception as e:
        print(f"[CRITICAL] Connection failed: {e}")

if __name__ == "__main__":
    print("Diagnosing Search Engine Config...")
    if not WHOOGLE_URL:
        print("[!] ERROR: WHOOGLE_URL is missing. Please edit your .env file.")
        sys.exit(1)

    print(f"URL: {WHOOGLE_URL}")

    if test_json():
        print("\n>>> OVERALL STATUS: EXCELLENT. JSON API is active.")
    else:
        print("\n>>> OVERALL STATUS: JSON Failed. Checking HTML capability...")
        test_html()
