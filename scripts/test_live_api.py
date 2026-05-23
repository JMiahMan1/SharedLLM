import os
import sys
import httpx

BASE_URL = os.getenv("LIVE_TEST_URL", "http://localhost:8080")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
API_KEY = os.getenv("API_KEY") # Optional: can use Bearer token instead

HEADERS = {}
if API_KEY:
    HEADERS["Authorization"] = f"Bearer {API_KEY}"
elif INTERNAL_SECRET:
    HEADERS["X-Internal-Secret"] = INTERNAL_SECRET

ENDPOINTS = [
    ("GET", "/health/ready", "Global Readiness"),
    ("GET", "/api/logs?limit=5", "Live Logs (Logging Service)"),
    ("GET", "/api/users", "Users (Identity Service)"),
    ("GET", "/api/workspaces", "Workspaces (Workspace Runtime)"),
    ("GET", "/api/config", "Gateway Config (Gateway Service)"),
    ("GET", "/api/admin/raven/config", "Raven Config (Execution/Gateway)"),
]

def run_live_tests():
    print(f"=== Starting Live API Verification for {BASE_URL} ===\n")
    
    with httpx.Client(base_url=BASE_URL, headers=HEADERS, timeout=10.0) as client:
        success = True
        
        for method, path, description in ENDPOINTS:
            print(f"Testing {description} ({method} {path})...")
            try:
                resp = client.request(method, path)
                if resp.status_code == 200:
                    print(f"  [SUCCESS] Status: {resp.status_code}")
                    # Optionally print a snippet of the data
                    data = resp.json()
                    if isinstance(data, list):
                        print(f"    Returned {len(data)} items")
                        if "Logs" in description:
                            for log in data:
                                print(f"      - {log.get('service')}: {log.get('message')}")
                    elif isinstance(data, dict):
                        print(f"    Returned a dict with keys: {list(data.keys())}")
                else:
                    print(f"  [FAILED] Status: {resp.status_code}")
                    print(f"    Response: {resp.text}")
                    success = False
            except Exception as e:
                print(f"  [ERROR] {e}")
                success = False
            print("")
            
        if success:
            print(f"=== All Live API Tests Passed! ===")
            sys.exit(0)
        else:
            print(f"=== Some Live API Tests Failed! ===")
            sys.exit(1)

if __name__ == "__main__":
    run_live_tests()
