import requests
import sys

REMOTE_URL = "http://ai.local:11435/api/admin/logs?lines=500"

try:
    print(f"Fetching logs from {REMOTE_URL}...")
    r = requests.get(REMOTE_URL, timeout=10)
    if r.status_code == 200:
        data = r.json()
        logs = data.get("logs", [])
        print("--- REMOTE LOGS START ---")
        for line in logs:
            print(line.strip())
        print("--- REMOTE LOGS END ---")
    else:
        print(f"Failed to fetch logs: {r.status_code} {r.text}")
except Exception as e:
    print(f"Connection error: {e}")
