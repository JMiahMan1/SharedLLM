import requests
import json
import logging
import sys
import time

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("RemoteTest")

REMOTE_URL = "http://192.168.2.211:11435/api/chat"

def remote_query(query: str):
    """Sends a query to the remote API and returns the full response content."""
    log.info(f"\n>> Sending Query: '{query}'")
    payload = {"query": query}
    try:
        response = requests.post(REMOTE_URL, json=payload, timeout=90)
        if response.status_code == 200:
            data = response.json()
            content = data.get("message", {}).get("content", "")
            log.info(f"<< Response: {content[:200]}...") # Log preview
            return content
        else:
            log.error(f"<< HTTP Error {response.status_code}: {response.text}")
            return None
    except Exception as e:
        log.error(f"<< Connection Failed: {e}")
        return None

def main():
    log.info("--- Starting Natural Search & Play Test ---")
    
    try:
        # Step 1: Search for Content (Mimicking human browsing)
        search_query = "Search for songs by Pink"
        response_text = remote_query(search_query)
        
        if not response_text:
            log.error("Search failed or timed out. Aborting.")
            return

        # Step 2: Extract a song/artist from response to Play
        target_song = "Pink" 
        
        # Step 3: Send Play Command
        play_query = f"Play {target_song} on Office TV"
        log.info(f"--- Decided to play: '{play_query}' ---")
        
        play_response = remote_query(play_query)
        
        if play_response and "Sent command to play" in play_response:
            log.info("SUCCESS: Playback command accepted.")
        elif play_response:
             log.info(f"RESULT: {play_response}")
        else:
            log.error("FAILURE: Playback command failed.")
            
        # Wait for a few seconds to let it play/buffer
        time.sleep(10)

    finally:
        log.info("\n--- Teardown: Restoring Device State ---")
        stop_query = "Turn off Office TV"
        log.info(f">> Sending Cleanup Query: '{stop_query}'")
        remote_query(stop_query)
        log.info("Cleanup command sent.")

if __name__ == "__main__":
    main()
