import requests
import os

url = "http://192.168.1.161:11434/api/generate"
payload = {
    "model": "qwen3:latest",
    "prompt": "Hello",
    "stream": False
}

print(f"Testing URL: '{url}'")
print(f"Env HTTP_PROXY: {os.environ.get('HTTP_PROXY')}")
print(f"Env HTTPS_PROXY: {os.environ.get('HTTPS_PROXY')}")
print(f"Env NO_PROXY: {os.environ.get('NO_PROXY')}")

try:
    resp = requests.post(url, json=payload, timeout=5)
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text[:100]}")
except Exception as e:
    print(f"Exception: {e}")
