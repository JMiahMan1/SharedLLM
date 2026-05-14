import requests
import json

url = "http://localhost:11435/api/chat"
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
    except:
        print("Raw Response:", resp.text)
except Exception as e:
    print("Error:", e)

