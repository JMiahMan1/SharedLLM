import requests
import os

url = os.getenv("JARVIS_BASE_URL", "http://localhost:8080/api/chat")
payload = {
    "query": "Execute the StorageIndexRequest tool for the path /Notes",
    "user_id": "jeremiah",
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

