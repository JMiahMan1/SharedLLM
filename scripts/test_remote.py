import json
import os

import requests

url = os.getenv("JARVIS_BASE_URL", "http://localhost:8080/api/chat")
payload = {
    "query": "Execute the StorageIndexRequest tool for the path /Notes",
    "user_id": "jeremiah",
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

