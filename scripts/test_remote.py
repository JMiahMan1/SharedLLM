import json
import os
import sys

import requests

LIVE_TEST_USER = os.environ.get("LIVE_TEST_USER") or sys.exit("Set LIVE_TEST_USER to the Jarvis username to test as.")

url = os.getenv("JARVIS_BASE_URL", "http://localhost:8080/api/chat")
payload = {
    "query": "Execute the StorageIndexRequest tool for the path /Notes",
    "user_id": LIVE_TEST_USER,
    "stream": False
}

try:
    resp = requests.post(url, json=payload, timeout=60)
    print("Status Code:", resp.status_code)
    try:
        print(json.dumps(resp.json(), indent=2))
    except Exception:
        print("Raw Response:", resp.text)
except Exception as e:
    print("Error:", e)

