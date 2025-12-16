import unittest
import requests
import time
import os
import warnings
from urllib3.exceptions import InsecureRequestWarning
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Suppress insecure request warnings for self-signed certs/local IPs
warnings.simplefilter('ignore', InsecureRequestWarning)

# --- Configuration ---
API_URL = os.getenv("API_URL", "http://localhost:11435") # Default to localhost if running on server
NEXTCLOUD_URL = os.getenv("NEXTCLOUD_URL", "")
NEXTCLOUD_USER = os.getenv("NEXTCLOUD_USER", "")
NEXTCLOUD_PASS = os.getenv("NEXTCLOUD_PASS", "")

HEADERS = {
    "Content-Type": "application/json",
    "X-RAG-User": NEXTCLOUD_USER if NEXTCLOUD_USER else "admin",
    "User-Agent": "IntegrationTestSuite"
}

class UnifiedRAGIntegrationTest(unittest.TestCase):
    
    def setUp(self):
        """Pre-check: Ensure API is reachable."""
        try:
            r = requests.get(f"{API_URL}/health", timeout=5)
            if r.status_code != 200:
                self.fail(f"API is down: {r.status_code}")
        except requests.exceptions.ConnectionError:
            self.fail(f"API unreachable at {API_URL}")

    # --- HELPER METHODS ---
    def send_chat(self, content, timeout=120): # increased timeout for LLM
        payload = {
            "messages": [{"role": "user", "content": content}],
            "stream": False
        }
        try:
            r = requests.post(f"{API_URL}/api/chat", json=payload, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                json_resp = r.json()
                # Handle OpenAI-style vs Simple format
                if "choices" in json_resp:
                    return json_resp["choices"][0]["message"]["content"]
                return json_resp.get("response") or json_resp.get("message", {}).get("content", "")
            return f"Error: {r.status_code} {r.text}"
        except Exception as e:
            return f"Exception: {str(e)}"

    def get_ha_state(self, entity_id):
        try:
            r = requests.get(f"{API_URL}/api/ha/state/{entity_id}", headers=HEADERS, timeout=5)
            if r.status_code == 200:
                return r.json().get("state")
        except: pass
        return "unknown"

    # --- TESTS ---

    def test_00_infrastructure_verify(self):
        """Verify DB Collections and Redis are accessible."""
        print("\n[TEST] Infrastructure Verification")
        
        # 1. Verify ChromaDB Collections
        # We need a way to list collections. The API doesn't expose a direct "list collections" endpoint,
        # but we can infer existence by searching them or checking the health with a deeper probe if available.
        # Since we just updated the code to use PersistentClient, let's assume if health is OK, CLIENT is OK.
        # To strictly follow the user request, we will try to search SPECIFIC sources.
        
        # Check Home Assistant Collection
        r_ha = requests.get(f"{API_URL}/api/rag/search", params={"q": "test", "k": 1, "source": "ha"}, headers=HEADERS)
        self.assertEqual(r_ha.status_code, 200, "Failed to query HA collection")
        # We don't assert results > 0 because it might be empty on fresh install, but the 200 OK means the collection exists/is queryable.
        
        # Check Nextcloud Collection
        r_nc = requests.get(f"{API_URL}/api/rag/search", params={"q": "test", "k": 1, "source": "nextcloud"}, headers=HEADERS)
        self.assertEqual(r_nc.status_code, 200, "Failed to query Nextcloud collection")

        # 2. Verify Redis (via Timer List)
        # Timer list uses Redis. If this returns 200 (even empty list), Redis is up.
        r_redis = requests.get(f"{API_URL}/api/timer/list", timeout=5)
        self.assertEqual(r_redis.status_code, 200, "Redis check failed (Timer list endpoint returned error)")
        print("   [PASS] Chroma Collections (ha, nextcloud) & Redis reachable.")

    def test_01_health_check(self):
        """Verify API health and DB connection."""
        print("\n[TEST] Health Check")
        r = requests.get(f"{API_URL}/health")
        data = r.json()
        self.assertEqual(data.get("status"), "ok")
        self.assertTrue(data.get("db"), "Database connection (db: true) failed!")

    def test_02_entity_resolution_piano_lamp(self):
        """Verify 'Piano Lamp' resolves to 'light.piano_lamp'."""
        print("\n[TEST] Entity Resolution: Piano Lamp")
        # Direct search via RAG endpoint to verify resolution logic
        r = requests.get(f"{API_URL}/api/rag/search", params={"q": "Piano Lamp", "k": 3}, headers=HEADERS)
        self.assertEqual(r.status_code, 200)
        results = r.json().get("results", [])
        
        found = False
        for res in results:
            meta = res.get("metadata", {})
            if meta.get("entity_id") == "light.piano_lamp":
                found = True
                break
        
        self.assertTrue(found, "Could not find 'light.piano_lamp' in RAG search results for 'Piano Lamp'")

    def test_03_note_crud_lifecycle(self):
        """Create, Read, Append, Delete a Note."""
        print("\n[TEST] Note System Lifecycle")
        ts = int(time.time())
        title = f"TestNote_{ts}"
        content = f"Content_{ts}"
        
        # 1. Create
        resp = self.send_chat(f"Create a note called {title} that says {content}")
        self.assertRegex(resp.lower(), r"created|saved|success", f"Creation failed: {resp}")
        
        time.sleep(2) # propagation
        
        # 2. Read
        resp = self.send_chat(f"Read the note {title}")
        self.assertIn(content, resp, f"Read failed/mismatch: {resp}")
        
        # 3. Append
        resp = self.send_chat(f"Add 'Milk' to {title}")
        self.assertRegex(resp.lower(), r"added|success|appended", f"Append failed: {resp}")
        
        # 4. Read Again
        resp = self.send_chat(f"Read note {title}")
        self.assertIn("Milk", resp, "Appended content not found")
        
        # 5. Delete (Cleanup)
        resp = self.send_chat(f"Delete note {title}")
        self.assertRegex(resp.lower(), r"deleted|removed", f"Deletion failed: {resp}")

    def test_04_calendar_lifecycle(self):
        """Schedule, Update, Delete an Event."""
        print("\n[TEST] Calendar System Lifecycle")
        ts = int(time.time())
        summary = f"RAG_TestEvent_{ts}"
        
        # 1. Schedule
        resp = self.send_chat(f"Schedule {summary} tomorrow at 9am")
        self.assertIn("Scheduled", resp, f"Schedule failed: {resp}")
        
        time.sleep(2)
        
        # 2. Update
        resp = self.send_chat(f"Reschedule {summary} to tomorrow at 5pm")
        self.assertIn("Rescheduled", resp, f"Reschedule failed: {resp}")
        
        time.sleep(2)
        
        # 3. Delete (Cleanup)
        resp = self.send_chat(f"Cancel event {summary}")
        self.assertIn("Deleted", resp, f"Delete failed: {resp}")

    def test_05_timer_lifecycle(self):
        """Set and Delete a Timer."""
        print("\n[TEST] Timer System Lifecycle")
        
        # 1. Set Timer
        # Note: Using "5 minute timer" to ensure it doesn't fire immediately
        resp = self.send_chat("Set a timer for 15 minutes")
        self.assertIn("Timer set", resp, f"Timer set failed: {resp}")
        
        # 2. List Timers (to find ID/confirm)
        time.sleep(1)
        # We hit the raw API for stability here
        r = requests.get(f"{API_URL}/api/timer/list")
        timers = r.json()
        self.assertTrue(len(timers) > 0, "Timer list is empty after setting timer")
        
        # 3. Delete (Cleanup)
        # We delete all test expirations to be safe
        timer_id = timers[0]["id"]
        r = requests.post(f"{API_URL}/api/timer/delete", params={"timer_id": timer_id})
        self.assertEqual(r.status_code, 200)

if __name__ == '__main__':
    unittest.main()
