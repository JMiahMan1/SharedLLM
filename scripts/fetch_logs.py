
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
api_url = f"http://{api_url_raw}:11435" if not api_url_raw.startswith("http") else f"{api_url_raw}:11435"

# System logs are admin-only: an administrator's API key, from the environment.
api_key = os.getenv("JARVIS_API_KEY")
if not api_key:
    print("ERROR: JARVIS_API_KEY (an admin's API key) not set")
    sys.exit(1)

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}",
    "User-Agent": "LogFetcher"
}

def fetch_logs():
    try:
        url = f"{api_url}/api/admin/logs?limit=200"
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
