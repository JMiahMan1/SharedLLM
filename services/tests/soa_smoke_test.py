"""
SOA Smoke Test Script.
Performs end-to-end health checks and basic functional verification across multiple microservices.
Usage: python soa_smoke_test.py [BASE_URL] [INTERNAL_SECRET]
"""
import os
import sys

import requests

# Configuration
BASE_URL = sys.argv[1] if len(sys.argv) > 1 else os.getenv("GATEWAY_URL", "http://localhost:11435")
INTERNAL_SECRET = sys.argv[2] if len(sys.argv) > 2 else os.getenv("INTERNAL_SECRET", "test-secret-12345")

print(f"--- SharedLLM Smoke Test Targeting: {BASE_URL} ---")

def test_health():
    print("\n[Health Checks]")
    # Global readiness (this checks all downstream services via Gateway)
    try:
        resp = requests.get(f"{BASE_URL}/health/ready", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print("  - Global Ready: YES")
            for svc, status in data.get("services", {}).items():
                print(f"    - {svc.ljust(10)}: {status}")
        else:
            print(f"  - Global Ready: NO ({resp.status_code})")
    except Exception as e:
        print(f"  - Global Ready: FAILED ({e})")

def test_intent_classification():
    print("\n[Intent Routing]")
    payload = {
        "messages": [{"role": "user", "content": "Turn off piano lamp"}],
        "model": "qwen3:latest",
        "rag_user": "default"
    }
    try:
        resp = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=10)
        print(f"  - Status Code: {resp.status_code}")
        data = resp.json()
        if "status" in data:
            print(f"  - Result     : {data['status']}")
            print(f"  - Intent     : {data.get('intent', 'unknown')}")
            print(f"  - Confidence : {data.get('confidence', 0):.2f}")
        else:
            print("  - Warning: Slow Path triggered.")
    except Exception as e:
        print(f"  - Request failed: {e}")

if __name__ == "__main__":
    test_health()
    test_intent_classification()
