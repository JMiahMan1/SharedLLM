import requests
import os
import sys

# Configuration
GATEWAY_URL = "http://localhost:11435"
INTERNAL_SECRET = "test-secret-12345"

def test_health():
    print("--- Testing Service Health ---")
    try:
        resp = requests.get(f"{GATEWAY_URL}/health")
        print(f"Gateway Health: {resp.status_code} - {resp.json()}")
    except Exception as e:
        print(f"Gateway unreachable: {e}")

def test_intent_classification():
    print("\n--- Testing Intent Classification (Fast Path) ---")
    payload = {
        "messages": [{"role": "user", "content": "Turn off piano lamp"}],
        "model": "qwen3:latest",
        "rag_user": "default"
    }
    try:
        resp = requests.post(f"{GATEWAY_URL}/api/chat", json=payload)
        print(f"Status Code: {resp.status_code}")
        data = resp.json()
        if "status" in data:
            print(f"Fast Path Result: {data['status']}")
            print(f"Message: {data.get('message')}")
            if "detail" in data:
                print(f"Error Detail: {data['detail']}")
        else:
            print("Slow Path (LLM) triggered - likely failed Fast Path threshold.")
            print(f"Response preview: {str(data)[:200]}...")
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    test_health()
    test_intent_classification()
