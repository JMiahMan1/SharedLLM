import requests
import json
import os
import sys

# Configuration
API_URL = os.getenv("API_URL", "http://192.168.2.211:11435")
HEADERS = {"Content-Type": "application/json"}

def test_music_playback():
    print(f"Testing Music Playback on {API_URL}...")
    
    # payload targeting the specific intent user asked for
    payload = {
        "text": "Play Brandon Lake on Office TV",
        "user_id": "jeremiah",
        "voice_id": "test_voice",
        "conversation_id": "test_conv_music_1"
    }
    
    try:
        resp = requests.post(f"{API_URL}/api/chat", json=payload, headers=HEADERS, timeout=30)
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
             # It might be a single JSON response if not streaming, but /api/chat usually streams
            print("\n[INFO] Check above output for success confirmation token or error.")

    except Exception as e:
        print(f"\n[FAILURE] Request failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_music_playback()
