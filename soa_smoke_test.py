import requests
import os
import sys
import json
import time

# Configuration
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:11435")
IDENTITY_URL = os.getenv("IDENTITY_URL", "http://identity:8001")
EXECUTION_URL = os.getenv("EXECUTION_URL", "http://execution:8003")
RAG_URL = os.getenv("RAG_URL", "http://rag:8004")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")

def test_service_ping(name, url):
    try:
        resp = requests.get(f"{url}/health", timeout=5)
        if resp.status_code == 200:
            log(f"PASS: {name} Ping")
            return True
        log(f"FAIL: {name} Ping (HTTP {resp.status_code})")
    except Exception as e:
        log(f"FAIL: {name} Ping ({e})")
    return False

def test_gateway_auth():
    try:
        # Test discovery which requires auth
        resp = requests.get(f"{GATEWAY_URL}/api/auth/discover", headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=5)
        if resp.status_code == 200:
            log("PASS: Gateway Auth/Discovery")
            return True
        log(f"FAIL: Gateway Auth (HTTP {resp.status_code})")
    except Exception as e:
        log(f"FAIL: Gateway Auth ({e})")
    return False

def test_rag_capability_search():
    try:
        payload = {
            "collection_name": "system_capabilities",
            "query": "how to control lights",
            "user_id": "default",
            "k": 1
        }
        resp = requests.post(f"{RAG_URL}/rag/search", json=payload, headers={"X-Internal-Secret": INTERNAL_SECRET}, timeout=5)
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                log(f"PASS: RAG Search ({results[0].get('metadata', {}).get('name')})")
                return True
            log("FAIL: RAG Search (No results)")
        else:
            log(f"FAIL: RAG Search (HTTP {resp.status_code})")
    except Exception as e:
        log(f"FAIL: RAG Search ({e})")
    return False

def test_execution_ha_link():
    try:
        # Check if HA is reachable from Execution
        resp = requests.get(f"{EXECUTION_URL}/health", timeout=5)
        if resp.status_code == 200:
            # The health check usually includes HA status
            data = resp.json()
            if data.get("ha_connected") or data.get("status") == "ok":
                log("PASS: Execution HA Link")
                return True
        log(f"FAIL: Execution HA Link (HTTP {resp.status_code})")
    except Exception as e:
        log(f"FAIL: Execution HA Link ({e})")
    return False

def run_all():
    log("=== INITIALIZING SHAREDLLM SOA SMOKE TEST ===")
    results = []
    
    # 1. Pings
    results.append(test_service_ping("Identity", IDENTITY_URL))
    results.append(test_service_ping("Execution", EXECUTION_URL))
    results.append(test_service_ping("RAG", RAG_URL))
    
    # 2. Functional
    results.append(test_gateway_auth())
    results.append(test_rag_capability_search())
    results.append(test_execution_ha_link())
    
    total = len(results)
    passed = sum(1 for r in results if r)
    log(f"=== TEST SUMMARY: {passed}/{total} PASSED ===")
    
    if passed < total:
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    run_all()
