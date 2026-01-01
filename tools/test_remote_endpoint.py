import requests
import json
import logging
import sys

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("RemoteTest")

REMOTE_URL = "http://192.168.2.211:11435/api/chat"
HEADERS = {"Content-Type": "application/json"}

def test_query(query: str, expected_device_substr: str = None, expected_intent: str = None):
    log.info(f"\n--- Testing Query: '{query}' ---")
    payload = {"query": query}
    
    try:
        response = requests.post(REMOTE_URL, json=payload, timeout=20)
        log.info(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            # The response format depends on the API. Usually it's a chat response.
            # We look for debug info or the text response describing the action.
            content = data.get("message", {}).get("content", "")
            log.info(f"Response: {content}")
            
            # Simple validation based on text response
            if expected_device_substr and expected_device_substr.lower() not in content.lower():
                 log.warning(f"FAILED: Expected device '{expected_device_substr}' not found in response.")
            elif expected_device_substr:
                 log.info(f"SUCCESS: Found expected device '{expected_device_substr}'.")
                 
            if expected_intent and expected_intent.lower() not in content.lower() and str(data).find(expected_intent) == -1:
                 # Intent might not be in user text, but maybe in logs/debug? 
                 # For now, just logging content.
                 pass

        else:
            log.error(f"Request Failed: {response.text}")
            
    except Exception as e:
        log.error(f"Connection Error: {e}")

def main():
    log.info(f"Targeting: {REMOTE_URL}")
    
    # 1. Ping / Connectivity
    try:
        requests.get("http://192.168.2.211:11435/api/ping", timeout=5)
        log.info("Ping Successful.")
    except:
        log.warning("Ping Failed (Method Not Allowed or Timeout), proceeding to Chat API.")

    try:
        # 2. Test "Watch" (Video)
        test_query("Watch Netflix on Office TV", expected_device_substr="Office TV") 
        # Ideally checking for "androidtv" or "via Roku" if response provides it.

        # 3. Test "Play" (Music)
        # Expecting Music Assistant or Cast, NOT Android TV native
        test_query("Play heavy metal on Office TV", expected_device_substr="Office TV")

        # 4. Test Power
        test_query("Turn off Office TV", expected_device_substr="Office TV")
        
    finally:
        log.info("\n--- Teardown: Restoring Device State ---")
        log.info("Sending 'Stop music on Office TV' to ensure device is clean...")
        test_query("Stop music on Office TV")
        log.info("Teardown complete.")

if __name__ == "__main__":
    main()
