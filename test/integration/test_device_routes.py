
import requests
import os
import sys
import time
from dotenv import load_dotenv

script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
load_dotenv(os.path.join(root_dir, '.env'))

API_URL = os.getenv("API_URL", "http://localhost:11435")
TIMEOUT = 300

def print_pass(msg): print(f"\033[92m[PASS]\033[0m {msg}")
def print_fail(msg): 
    print(f"\033[91m[FAIL]\033[0m {msg}")
    sys.exit(1)
def print_info(msg): print(f"\033[94m[INFO]\033[0m {msg}")

def test_device_routes():
    print_info(f"--- Direct Device Route Tests ({API_URL}) ---")

    # 1. Roku Routes (Direct)
    print_info("TEST 1: Roku Direct Routes")
    # We use a dummy IP so we don't actually toggle hardware, but we expect the ROUTE to exist
    # and return either Success (mocked) or Failure (timeout/connection), but 404 is the fail condition.
    payload = {"ip": "192.168.1.99", "app_id": "12"}
    r = requests.post(f"{API_URL}/api/roku/launch", json=payload, timeout=TIMEOUT)
    if r.status_code != 404:
        print_pass(f"Roku launch route exists (Status: {r.status_code})")
    else:
        print_fail("Roku launch route 404")

    # 2. WebOS Routes (Direct)
    print_info("TEST 2: WebOS Direct Routes")
    payload = {"ip": "192.168.1.99", "message": "Test Notification"}
    r = requests.post(f"{API_URL}/api/webos/notify", json=payload, timeout=TIMEOUT)
    if r.status_code != 404:
        print_pass(f"WebOS notify route exists (Status: {r.status_code})")
    else:
        print_fail("WebOS notify route 404")

    # 3. Android TV Routes (Direct)
    print_info("TEST 3: Android TV Direct Routes")
    payload = {"ip": "192.168.1.99", "app_link": "netflix://"}
    r = requests.post(f"{API_URL}/api/android/launch", json=payload, timeout=TIMEOUT)
    if r.status_code != 404:
        print_pass(f"Android launch route exists (Status: {r.status_code})")
    else:
        print_fail("Android launch route 404")

if __name__ == "__main__":
    try:
        test_device_routes()
    except Exception as e:
        print_fail(f"Exception: {e}")
