
import requests
import time
import sys
import json

API_URL = "http://localhost:8000/api/chat"

def test_office_music():
    print("--- Testing Office TV Music Playback ---")
    
    # Payload mimicking user request
    payload = {
        "query": "Play Brandon Lake on the Office TV",
        "user": "test_script",
        "model": "gpt-4o-mini" # or whatever default is, doesn't matter much for routing if semantic
    }
    
    # Wait for API to be responsive (just in case)
    for i in range(10):
        try:
            resp = requests.get("http://localhost:8000/health", timeout=2)
            if resp.status_code == 200:
                print("API is UP.")
                break
        except Exception:
            print(f"Waiting for API... ({i+1}/10)")
            time.sleep(2)
    else:
        print("API failed to come up.")
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
                print("❌ TEST FAILED: Response indicates failure.")
                sys.exit(1)
            else:
                print("✅ TEST PASSED: Response indicates success.")
        else:
            print(f"❌ TEST FAILED: HTTP {resp.status_code}")
            print(resp.text)
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ TEST FAILED: Exception: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_office_music()
