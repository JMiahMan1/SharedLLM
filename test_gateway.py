import requests

GATEWAY_URL = "http://localhost:11435"

def test_chat():
    payload = {
        "query": "Execute the StorageIndexRequest tool for the path /Notes",
        "user_id": "jeremiah",
        "stream": False
    }
    try:
        resp = requests.post(f"{GATEWAY_URL}/api/chat", json=payload, timeout=30)
        print("Status Code:", resp.status_code)
        print("Response:", resp.text)
    except Exception as e:
        print("Error:", e)

test_chat()
