
import requests
import time
import sys
import json

API_URL = "http://localhost:11435/api/chat"

def test_office_music():
    print("--- Testing Office TV Music Playback ---")
    
    # Payload mimicking user request
    payload = {
        "query": "Play Brandon Lake on the Office TV",
        "user": "test_script",
        "model": "gpt-4o-mini"
    }
    
    # Wait for API to be responsive (Robust Health Check)
    # 30 retries * 2s = 60s timeout
    print("Running Health Check...")
    for i in range(30):
        try:
            resp = requests.get("http://localhost:11435/health", timeout=2)
            if resp.status_code == 200:
                print("API is UP and Healthy.")
                break
        except Exception:
            pass # ignore conn errors
        
        if i % 5 == 0:
            print(f"Waiting for API... ({i+1}/30)")
        time.sleep(2)
    else:
        print("❌ CRITICAL: API failed to come up after 60 seconds.")
        sys.exit(1)

    # Send Request
    print(f"Sending Query: '{payload['query']}'")
    try:
        start = time.time()
        resp = requests.post(API_URL, json=payload, timeout=30)
        duration = time.time() - start
        
        print(f"Response Time: {duration:.2f}s")
        print(f"Status Code: {resp.status_code}")
        
        if resp.status_code == 200:
            data = resp.json()
            print("Response Data:")
            print(json.dumps(data, indent=2))
            
            # Check for success indicators
            # We expect a command confirmation or "started playing" message
            response_text = data.get("response", "") or data.get("message", {}).get("content", "")
            
            if "fail" in response_text.lower() or "error" in response_text.lower():
                print("❌ TEST FAILED: Response indicates failure/error.")
                sys.exit(1)
            else:
                print("✅ TEST PASSED: Response indicates success.")
        else:
            print(f"❌ TEST FAILED: HTTP {resp.status_code}")
            print(f"Response Body: {resp.text}")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ TEST FAILED: Exception: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_office_music()
