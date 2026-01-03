
import requests
import logging
from datetime import datetime

class BaseTest:
    def __init__(self, api_url, headers, logger):
        self.api_url = api_url
        self.headers = headers
        self.log_func = logger  # expects (test_name, status, message)

    def log(self, name, status, msg=""):
        self.log_func(name, status, msg)

    def safe_post(self, endpoint, payload, label):
        try:
            r = requests.post(f"{self.api_url}{endpoint}", json=payload, headers=self.headers, timeout=120)
            try:
                data = r.json()
                # Try various response formats
                msg = data.get("response") or data.get("message", {}).get("content") or data.get("msg") or str(data)
                return msg, r.status_code
            except:
                return r.text, r.status_code
        except Exception as e:
            self.log(label, "ERROR", f"Req failed: {e}")
            return None, 0
