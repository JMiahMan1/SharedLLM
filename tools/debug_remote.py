import requests
import sys

URL = "http://192.168.2.211:11435"

def check(endpoint, method="GET", payload=None):
    print(f"\n--- Checking {method} {URL}{endpoint} ---")
    try:
        if method == "GET":
            r = requests.get(f"{URL}{endpoint}", timeout=5)
        else:
            r = requests.post(f"{URL}{endpoint}", json=payload or {}, timeout=5)
        
        print(f"Status: {r.status_code}")
        print("Headers:")
        for k, v in r.headers.items():
            print(f"  {k}: {v}")
        
        print("\nBody Snippet:")
        print(r.text[:500])
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check("/health")
    check("/api/chat", method="POST", payload={"messages": [{"role": "user", "content": "hi"}]})
    check("/api/version")
