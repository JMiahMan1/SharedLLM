import os
import sys

import requests

LIVE_TEST_USER = os.environ.get("LIVE_TEST_USER") or sys.exit("Set LIVE_TEST_USER to the Jarvis username to test as.")

GATEWAY_URL = "http://localhost:11435"

def test_chat():
    payload = {
        "query": "Execute the StorageIndexRequest tool for the path /Notes",
        "user_id": LIVE_TEST_USER,
        "stream": False
    }
    try:
        resp = requests.post(f"{GATEWAY_URL}/api/chat", json=payload, timeout=30)
        print("Status Code:", resp.status_code)
        print("Response:", resp.text)
    except Exception as e:
        print("Error:", e)

test_chat()
