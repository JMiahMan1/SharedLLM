
import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()
api_url_raw = os.getenv("RAG_ADDRESS")

if not api_url_raw:
    print("ERROR: RAG_ADDRESS not set in .env")
    sys.exit(1)

# Ensure http prefix
if not api_url_raw.startswith("http"):
    api_url = f"http://{api_url_raw}:11435"
else:
    api_url = f"{api_url_raw}:11435"

HEADERS = {
    "Content-Type": "application/json",
    "X-RAG-User": "admin",
    "User-Agent": "LogFetcher"
}

def fetch_logs():
    try:
        url = f"{api_url}/api/logs?limit=200"
        print(f"Fetching logs from {url}...")
        resp = requests.get(url, headers=HEADERS, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            print("Logs retrieved successfully.")
            os.makedirs("temp", exist_ok=True)
            with open("temp/remote_server_logs.txt", "w") as f:
                for entry in data:
                    line = f"{entry.get('timestamp')} [{entry.get('level')}] [{entry.get('service')}] {entry.get('message')}"
                    f.write(line + "\n")
            print("Saved to temp/remote_server_logs.txt")
        else:
            print(f"Failed to fetch logs. Status: {resp.status_code}")
            print(resp.text)

    except Exception as e:
        print(f"Error fetching logs: {e}")

if __name__ == "__main__":
    fetch_logs()
