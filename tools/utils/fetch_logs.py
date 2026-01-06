
import requests
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()
API_URL = os.getenv("RAG_ADDRESS")

if not API_URL:
    print("ERROR: RAG_ADDRESS not set in .env")
    sys.exit(1)

# Ensure http prefix
if not API_URL.startswith("http"):
    API_URL = f"http://{API_URL}:11435"
else:
    API_URL = f"{API_URL}:11435"

HEADERS = {
    "Content-Type": "application/json",
    "X-RAG-User": "admin",
    "User-Agent": "LogFetcher"
}

def fetch_logs():
    try:
        url = f"{API_URL}/api/admin/logs?lines=200"
        print(f"Fetching logs from {url}...")
        resp = requests.get(url, headers=HEADERS, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            if "logs" in data:
                print("Logs retrieved successfully.")
                with open("temp/remote_server_logs.txt", "w") as f:
                    for line in data["logs"]:
                        f.write(line + "\n")
                print("Saved to temp/remote_server_logs.txt")
            else:
                print("No 'logs' key in response:", data)
        else:
            print(f"Failed to fetch logs. Status: {resp.status_code}")
            print(resp.text)
            
    except Exception as e:
        print(f"Error fetching logs: {e}")

if __name__ == "__main__":
    fetch_logs()
