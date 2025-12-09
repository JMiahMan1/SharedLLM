import requests
import json
import os
import sys

API_URL = os.getenv("API_URL", "http://192.168.2.211:11435")
HEADERS = {"Content-Type": "application/json"}

def test_batch_lights():
    print(f"Testing Batch Light Command on {API_URL}...")
    
    # "turn master bedroom lamps white" -> Should now execute batch set_color
    payload = {
        "query": "Turn master bedroom lamps white", 
        "user": "jeremiah",
        # "voice_id": "test_voice", # Not used by API
        # "conversation_id": "test_conv_batch_1" # Not used by API
    }
    
    try:
        resp = requests.post(f"{API_URL}/api/chat", json=payload, headers=HEADERS, timeout=45) # Increased timeout for batch
        resp.raise_for_status()
        
        print("\n--- Response Headers ---")
        print(resp.headers)
        
        print("\n--- Response Content ---")
        content = ""
        for line in resp.iter_lines(decode_unicode=True):
            if line:
                print(line)
                content += line
        
        if "data: [DONE]" in content:
            print("\n[SUCCESS] Stream completed successfully.")
        else:
            print("\n[INFO] Check output for batch success confirmation.")

    except requests.exceptions.HTTPError as he:
        print(f"\n[FAILURE] HTTP Error: {he}")
        print(f"Server Response: {he.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[FAILURE] Request failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_batch_lights()
