
import requests
import logging
from datetime import datetime

class BaseTest:
    def __init__(self, api_url, headers=None, logger=None):
        self.api_url = api_url
        self.headers = headers or {
            "Content-Type": "application/json",
            "X-RAG-User": "admin",
            "User-Agent": "SharedLLMTestRunner",
            "X-Include-Tool-Results": "true"
        }
        self.logger = logger  # expects (test_name, status, message)
        self.last_response_json = None

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
                
            # [Visibility Improvement] Show what we are asking
            if endpoint == "/api/chat" and "messages" in payload:
                last_msg = payload["messages"][-1].get("content", "")
                print(f"[TEST INPUT] '{last_msg}'")

            r = requests.post(f"{self.api_url}{endpoint}", json=payload, headers=self.headers, timeout=120)
            try:
                data = r.json()
                self.last_response_json = data # Store for inspection
                
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
    def get_entity_state(self, entity_id):
        """Fetch the live state of an entity from HA Proxy."""
        try:
            r = requests.get(f"{self.api_url}/api/ha/state/{entity_id}", headers=self.headers, timeout=5)
            if r.status_code == 200:
                data = r.json()
                return data.get("state")
            return None
        except Exception as e:
            self.log(f"State Check {entity_id}", "ERROR", f"Failed: {e}")
            return None
