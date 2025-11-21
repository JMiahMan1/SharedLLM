import requests
import json
import time
import sys
import os
from dotenv import load_dotenv

# --- Load .env from the Parent (Root) Directory ---
# Get the directory where this script is located
script_dir = os.path.dirname(os.path.abspath(__file__))
# Go up one level to the root directory
root_dir = os.path.dirname(script_dir)
# Construct path to .env
env_path = os.path.join(root_dir, '.env')

print(f"Loading config from: {env_path}")
load_dotenv(env_path)

# --- Configuration ---
# Defaults to localhost if not found in .env
API_URL = os.getenv("API_URL", "http://localhost:11435")
HEADERS = {
    "Content-Type": "application/json",
    "X-RAG-User": os.getenv("TEST_USER", "TestAdmin"),
    "User-Agent": "Mozilla/5.0 (Test Script)" # Simulates Chat Mode
}

def print_header(title):
    print(f"\n{'='*60}")
    print(f"TEST: {title}")
    print(f"{'='*60}")

def send_chat(prompt):
    print(f"Sending Prompt: '{prompt}'")
    start = time.time()
    try:
        # Using the non-streaming endpoint for easier testing
        url = f"{API_URL}/api/chat"
        payload = {
            "model": "qwen3:latest", # Or your default model
            "messages": [{"role": "user", "content": prompt}],
            "stream": False 
        }
        
        response = requests.post(url, headers=HEADERS, json=payload, timeout=60)
        response.raise_for_status()
        
        data = response.json()
        duration = time.time() - start
        
        # Extract content based on response format
        content = ""
        if "message" in data:
            content = data["message"]["content"]
        elif "choices" in data:
            content = data["choices"][0]["message"]["content"]
        else:
            content = str(data)
            
        print(f"Response ({duration:.2f}s):")
        print(f"  {content}")
        return content

    except Exception as e:
        print(f"FAILED: {e}")
        return None

def main():
    print(f"Starting Live Test on {API_URL}...\n")

    # 1. Information / Context Test
    print_header("1. Context & RAG Info")
    send_chat("What is the current status of the Piano Lamp and Office TV?")
    time.sleep(2)

    # 2. Simple Control: Turn ON
    print_header("2. Control: Turn ON Piano Lamp")
    send_chat("Turn on the Piano Lamp")
    time.sleep(2)

    # 3. Simple Control: Turn OFF
    print_header("3. Control: Turn OFF Piano Lamp")
    send_chat("Turn off the Piano Lamp")
    time.sleep(2)

    # 4. Media Control: Play Specific Music
    print_header("4. Media: Play Artist (Music Assistant)")
    send_chat("Play Brandon Lake on the Office TV")
    time.sleep(5)

    # 5. Media Control: Stop
    print_header("5. Media: Stop Playback")
    send_chat("Stop the Office TV")
    time.sleep(2)

    # 6. Complex Multi-Command
    print_header("6. Multi-Command Decomposition")
    send_chat("Turn on the Piano Lamp and play Brandon Lake on the Office TV")
    
    print("\n" + "="*60)
    print("TEST SEQUENCE COMPLETE")
    print("Please verify physically if devices responded.")
    print("="*60)

if __name__ == "__main__":
    try:
        # Quick health check
        r = requests.get(f"{API_URL}/health", timeout=2)
        if r.status_code == 200:
            print("API is Online and Healthy. Starting tests...")
            main()
        else:
            print(f"API returned status {r.status_code}. Aborting.")
    except requests.exceptions.ConnectionError:
        print(f"Could not connect to {API_URL}. Is the container running?")
