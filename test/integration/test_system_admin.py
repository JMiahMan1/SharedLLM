
import requests
import os
import sys
import time
from dotenv import load_dotenv

script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
load_dotenv(os.path.join(root_dir, '.env'))

API_URL = os.getenv("API_URL", "http://localhost:11435")
HEADERS = {"Content-Type": "application/json", "X-RAG-User": "admin"}
TIMEOUT = 300

def print_pass(msg): print(f"\033[92m[PASS]\033[0m {msg}")
def print_fail(msg): 
    print(f"\033[91m[FAIL]\033[0m {msg}")
    sys.exit(1)
def print_info(msg): print(f"\033[94m[INFO]\033[0m {msg}")

def check_status(response, expected_code=200):
    if response.status_code == expected_code:
        return True
    print(f"Expected {expected_code}, got {response.status_code}. Body: {response.text[:200]}")
    return False

def test_system_admin():
    print_info(f"--- System & Admin API Tests ({API_URL}) ---")

    # 1. Health & Version
    print_info("TEST 1: Health & Version Checks")
    r = requests.get(f"{API_URL}/health", timeout=TIMEOUT)
    if check_status(r): print_pass("Health check passed")
    else: print_fail("Health check failed")

    r = requests.get(f"{API_URL}/api/version", timeout=TIMEOUT)
    if check_status(r) and "version" in r.json(): print_pass(f"Version: {r.json()['version']}")
    else: print_fail("Version check failed")

    # 2. Admin Logs
    print_info("TEST 2: Admin Logs")
    r = requests.get(f"{API_URL}/api/admin/logs?lines=10", headers=HEADERS, timeout=TIMEOUT)
    if check_status(r) and "logs" in r.json():
        print_pass("Retrieved admin logs")
    else:
        print_fail("Failed to retrieve logs")

    # 3. RAG Management (List)
    print_info("TEST 3: RAG Management (List)")
    r = requests.get(f"{API_URL}/api/rag/list", headers=HEADERS, timeout=TIMEOUT)
    if check_status(r):
        print_pass("Listed RAG documents")
    else:
        print_fail("Failed to list RAG docs")

    # 4. Ingestion Triggers (Dry Run / Validation)
    # We won't run full ingestion as it's heavy, but we can hit the endpoint to ensure it's registered
    # Note: Triggering ingestion might be blocking, so we'll just check if the endpoint exists via a OPTIONS or 405 check
    # faster: just check if method not allowed or 422 for missing params, proving route exists
    print_info("TEST 4: Ingestion Route Check")
    r = requests.post(f"{API_URL}/ingest/nextcloud", headers=HEADERS, json={}, timeout=TIMEOUT)
    # Expect 200 (triggered) or 400/422 (validation), but NOT 404
    if r.status_code != 404:
        print_pass(f"Ingestion route exists (Status: {r.status_code})")
    else:
        print_fail("Ingestion route returned 404")

    # 5. Intent Export
    print_info("TEST 5: Intent Export")
    r = requests.get(f"{API_URL}/api/intent/export", headers=HEADERS, timeout=TIMEOUT)
    if check_status(r):
        print_pass("Intent export successful")
    else:
        print_fail("Intent export failed")

if __name__ == "__main__":
    try:
        test_system_admin()
    except Exception as e:
        print_fail(f"Exception during test: {e}")
