
import requests
import json
import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Configuration
API_URL = "http://192.168.2.211:11435"
USER = "jeremiah"
DEVICE_NAME = "Office Speaker" # The MA device
MEDIA_QUERY = "The Dark Side of the Moon" # Known working album/song

def call_rag_api(endpoint, payload):
    url = f"{API_URL}{endpoint}"
    try:
        response = requests.post(url, json=payload, timeout=60) # High timeout for MA search
        if response.status_code == 200:
            try:
                data = response.json()
                print("\nResponse Data:")
                print(json.dumps(data, indent=2))
                return data
            except json.JSONDecodeError:
                print(f"Response Text (not JSON): {response.text}")
                return None
        else:
            print(f"Failed: {response.status_code}")
            print(response.text)
            return None
    except Exception as e:
        print(f"Error calling {endpoint}: {e}")
        return None

def test_play_media():
    print(f"\n--- Testing Play Media: '{MEDIA_QUERY}' on '{DEVICE_NAME}' ---")
    payload = {
        "query": f"Play {MEDIA_QUERY} on {DEVICE_NAME}",
        "user": USER # 'user' matches the schema too
    }
    result = call_rag_api("/api/chat", payload)
    
    if result:
        print(f"Response: {result.get('response')}")
        # Check logs manually for success, or inspect HA state if we had access here
    else:
        print("FAILED: No response from API")

if __name__ == "__main__":
    test_play_media()
