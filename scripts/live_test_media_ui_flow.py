#!/usr/bin/env python3
# scripts/live_test_media_ui_flow.py
import os
import sys
import httpx

BASE_URL = os.getenv("LIVE_TEST_URL", "http://192.168.2.205:8080")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

def log_test(name, success, info=""):
    status = "SUCCESS" if success else "FAILED"
    print(f"[{status}] {name} {info}")

def run_tests():
    print(f"=== Starting Media & UI API Flow Verification on {BASE_URL} ===")
    
    # 1. Login to retrieve Token
    login_url = f"{BASE_URL}/api/auth/login"
    try:
        resp = httpx.post(login_url, json={"username": "default", "password": "admin"}, timeout=10.0)
        if resp.status_code == 200:
            token = resp.json().get("api_key") or resp.json().get("token")
            log_test("Auth Login", True, f"Token: {token[:10]}..." if token else "No token in response")
        else:
            log_test("Auth Login", False, f"Status: {resp.status_code}, Body: {resp.text}")
            sys.exit(1)
    except Exception as e:
        log_test("Auth Login", False, f"Exception: {e}")
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Internal-Secret": INTERNAL_SECRET
    }

    success = True

    # 2. Test Music Assistant Playlists
    try:
        resp = httpx.get(f"{BASE_URL}/api/media/music-assistant/playlists", headers=headers, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            is_valid = "status" in data and "playlists" in data
            log_test("Music Assistant Playlists", is_valid, f"Playlists count: {len(data.get('playlists', [])) if is_valid else 'invalid schema'}")
            if not is_valid:
                success = False
        else:
            log_test("Music Assistant Playlists", False, f"Status: {resp.status_code}, Body: {resp.text}")
            success = False
    except Exception as e:
        log_test("Music Assistant Playlists", False, f"Exception: {e}")
        success = False

    # 3. Test Music Assistant Recent
    try:
        resp = httpx.get(f"{BASE_URL}/api/media/music-assistant/recent", headers=headers, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            is_valid = "status" in data and "recent" in data
            log_test("Music Assistant Recent", is_valid, f"Recent count: {len(data.get('recent', [])) if is_valid else 'invalid schema'}")
            if not is_valid:
                success = False
        else:
            log_test("Music Assistant Recent", False, f"Status: {resp.status_code}, Body: {resp.text}")
            success = False
    except Exception as e:
        log_test("Music Assistant Recent", False, f"Exception: {e}")
        success = False

    # 4. Test Audiobookshelf Libraries
    try:
        resp = httpx.get(f"{BASE_URL}/api/media/audiobookshelf/libraries", headers=headers, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            is_valid = "status" in data and "libraries" in data
            log_test("Audiobookshelf Libraries", is_valid, f"Libraries count: {len(data.get('libraries', [])) if is_valid else 'invalid schema'}")
            if not is_valid:
                success = False
        else:
            log_test("Audiobookshelf Libraries", False, f"Status: {resp.status_code}, Body: {resp.text}")
            success = False
    except Exception as e:
        log_test("Audiobookshelf Libraries", False, f"Exception: {e}")
        success = False

    # 5. Test Audiobookshelf Last Played
    try:
        resp = httpx.get(f"{BASE_URL}/api/media/audiobookshelf/last-played", headers=headers, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            is_valid = "status" in data
            log_test("Audiobookshelf Last Played", is_valid, f"Status: {data.get('status')}")
            if not is_valid:
                success = False
        else:
            log_test("Audiobookshelf Last Played", False, f"Status: {resp.status_code}, Body: {resp.text}")
            success = False
    except Exception as e:
        log_test("Audiobookshelf Last Played", False, f"Exception: {e}")
        success = False

    # 6. Test Entity Search (HA/MA/ABS Proxy)
    try:
        payload = {"query": "", "domain": "media_player"}
        resp = httpx.post(f"{BASE_URL}/execute/entity/search", json=payload, headers=headers, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            is_valid = "result" in data
            log_test("Entity Search Proxy", is_valid, f"Entities found: {len(data.get('result', [])) if is_valid else 'invalid schema'}")
            if not is_valid:
                success = False
        else:
            log_test("Entity Search Proxy", False, f"Status: {resp.status_code}, Body: {resp.text}")
            success = False
    except Exception as e:
        log_test("Entity Search Proxy", False, f"Exception: {e}")
        success = False

    if success:
        print("=== All Media & UI API Flow Tests Passed! ===")
        sys.exit(0)
    else:
        print("=== Some Media & UI API Flow Tests Failed! ===")
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
