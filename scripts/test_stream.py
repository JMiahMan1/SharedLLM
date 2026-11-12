import os
import sys

import requests

LIVE_TEST_USER = os.environ.get("LIVE_TEST_USER") or sys.exit("Set LIVE_TEST_USER to the Jarvis username to test as.")

url = os.getenv("JARVIS_BASE_URL", "http://localhost:8080/api/chat")
payload = {
    "query": "Execute the StorageIndexRequest tool for the path /Notes",
    "user_id": LIVE_TEST_USER,
    "stream": True
}

try:
    with requests.post(url, json=payload, stream=True, timeout=60) as r:
        print("Status:", r.status_code)
        for line in r.iter_lines():
            if line:
                print(line.decode('utf-8'))
except Exception as e:
    print("Error:", e)

