
import requests
import logging
from datetime import datetime

class BaseTest:
    def __init__(self, api_url, headers=None, logger=None):
        self.api_url = api_url
        self.headers = headers or {
            "Content-Type": "application/json",
            "X-RAG-User": "admin",
            "User-Agent": "SharedLLMTestRunner"
        }
        self.logger = logger  # expects (test_name, status, message)

    def log(self, name, status, msg=""):
        if self.logger:
            self.logger(name, status, msg)
        else:
            print(f"[{status}] {name}: {msg}")

    def safe_post(self, endpoint, payload, label):
        try:
            # Ensure "admin" user is set if not provided in payload
            if "user" not in payload and endpoint == "/api/chat":
                payload["user"] = "admin"
                
            r = requests.post(f"{self.api_url}{endpoint}", json=payload, headers=self.headers, timeout=120)
            try:
                data = r.json()
                # Try various response formats (Pipeline vs Chat)
                msg = data.get("response") or \
                      (data.get("message") or {}).get("content") or \
                      data.get("msg") or \
                      str(data)
                return msg, r.status_code
            except:
                return r.text, r.status_code
        except Exception as e:
            self.log(label, "ERROR", f"Req failed: {e}")
            return None, 0
