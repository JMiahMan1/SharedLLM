
import os
import time
import caldav
import requests
import warnings
from datetime import datetime, timedelta
from dotenv import load_dotenv
from urllib3.exceptions import InsecureRequestWarning

# Internal imports for direct access when possible, though strictly tests should use API
from settings import HA_URL, HA_ENV_TOKEN, NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASS

warnings.simplefilter('ignore', InsecureRequestWarning)

class TestRunner:
    def __init__(self, api_url="http://127.0.0.1:11435"):
        self.api_url = api_url
        self.headers = {"Content-Type": "application/json", "X-RAG-User": "admin", "User-Agent": "ServerSideTest"}
        self.results = []
    
    def log(self, test_name, status, message=""):
        self.results.append({
            "test": test_name,
            "status": status,
            "message": message,
            "timestamp": datetime.now().isoformat()
        })

    def safe_post(self, endpoint, payload, label):
        try:
            r = requests.post(f"{self.api_url}{endpoint}", json=payload, headers=self.headers, timeout=120)
            try:
                msg = r.json().get("response", "") or r.json().get("message", {}).get("content", "")
                return msg
            except:
                return r.text
        except Exception as e:
            self.log(label, "ERROR", f"Req failed: {e}")
            return None

    def run_all(self):
        self.results = []
        self.log("SETUP", "INFO", f"Starting tests against {self.api_url}")
        
        # 1. Health
        try:
            r = requests.get(f"{self.api_url}/health", timeout=5)
            if r.status_code == 200:
                self.log("Health Check", "PASS")
            else:
                self.log("Health Check", "FAIL", f"Status {r.status_code}")
        except Exception as e:
            self.log("Health Check", "FAIL", str(e))
            return self.results # Abort if health fails

        # 2. History Context
        self._test_history()

        # 3. Calendar
        if NEXTCLOUD_URL and NEXTCLOUD_USER:
            self._test_calendar()
        else:
            self.log("Calendar", "SKIP", "Missing NC Config")

        # 4. Notes
        if NEXTCLOUD_URL and NEXTCLOUD_USER:
            self._test_notes()
        else:
            self.log("Notes", "SKIP", "Missing NC Config")

        return self.results

    def _test_history(self):
        q1 = "Who is the president of France?"
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":q1}], "stream":False}, "History Turn 1")
        q2 = "What is his wife's name?"
        r2 = self.safe_post("/api/chat", {"messages":[{"role":"user","content":q2}], "stream":False}, "History Turn 2")
        if r2 and ("macron" in r2.lower() or "brigitte" in r2.lower()):
            self.log("Multi-Turn Context", "PASS")
        else:
            self.log("Multi-Turn Context", "FAIL", f"Response: {r2[:100]}...")

    def _test_calendar(self):
        title = f"ServerTest_{int(time.time())}"
        
        # Add
        r = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Schedule {title} tomorrow at 9am"}], "stream":False}, "Cal Add")
        if r and "Scheduled" in r:
            self.log("Calendar Add", "PASS")
        else:
            self.log("Calendar Add", "FAIL", str(r)[:100])
        
        # Delete
        time.sleep(1)
        r = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Cancel {title}"}], "stream":False}, "Cal Delete")
        if r and "Deleted" in r:
            self.log("Calendar Delete", "PASS")
        else:
            self.log("Calendar Delete", "FAIL", str(r)[:100])

    def _test_notes(self):
        title = f"ServerNote_{int(time.time())}"
        # Create
        r = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Create note {title} saying hello"}], "stream":False}, "Note Create")
        if r and ("success" in r.lower() or "created" in r.lower()):
            self.log("Note Create", "PASS")
        else:
            self.log("Note Create", "FAIL", str(r)[:100])
        
        # Read
        time.sleep(1)
        r = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Read note {title}"}], "stream":False}, "Note Read")
        if r and "hello" in r.lower():
            self.log("Note Read", "PASS")
        else:
            self.log("Note Read", "FAIL", str(r)[:100])

runner = TestRunner()
