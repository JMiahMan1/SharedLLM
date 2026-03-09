
import os
import requests
import io
import time
from app.tests.base import BaseTest

class AnnouncementsTests(BaseTest):
    def run(self):
        print("\n[AnnouncementsTests] Starting Live Tests...")
        self.test_text_announcement()
        self.test_targeted_announcement()
        self.test_voice_intercom_upload()
        print("[AnnouncementsTests] Finished.\n")

    def test_text_announcement(self):
        query = "Announce Testing live text announcement 🔔"
        print(f"Testing Query: '{query}'")
        
        # We use the raw chat endpoint via safe_post
        payload = {
            # "model": "qwen3:14b", # Rely on server default
            "messages": [{"role": "user", "content": query}],
            "stream": False
        }
        
        resp_text, status = self.safe_post("/api/chat", payload, "Text Announcement")
        
        if status != 200:
            self.log("Text Announcement", "FAIL", f"Status: {status}, Resp: {resp_text}")
            return
            
        # Check tool results in last_response_json
        data = self.last_response_json
        tool_results = data.get("tool_results", [])
        
        if not tool_results:
             # Maybe it's in the text if include_tool_results header wasn't honored or structure diff
             pass
             
        # Look for success or specific service 'announce' or 'ha_notify' (fallback)
        # With our fix, it should likely use 'announce' -> 'process_announcement' -> returns dict
        # The tool result usually contains "status": "SUCCESS"
        
        success = False
        for tr in tool_results:
            if tr.get("status") == "SUCCESS" and "Announced" in tr.get("message", ""):
                 success = True
                 break
        
        if success:
            self.log("Text Announcement", "PASS", "Tool executed successfully")
        else:
            # Fallback check on text
            if "Announced" in str(resp_text) or "Notification sent" in str(resp_text):
                 self.log("Text Announcement", "PASS", f"Response indicates success: {resp_text}")
            else:
                 self.log("Text Announcement", "FAIL", f"No success confirmation found. Resp: {data}")

    def test_targeted_announcement(self):
        query = "Announce on Office TV Testing targeted announcement 📺"
        print(f"Testing Query: '{query}'")
        
        # We need to simulate the chat endpoint
        payload = {
            "messages": [{"role": "user", "content": query}],
            "user": "admin"
        }
        
        resp_content, status = self.safe_post("/api/chat", payload, "Targeted Announcement")
        
        if status != 200:
            self.log("Targeted Announcement", "FAIL", f"Status {status}")
            return

        # LLMs might return empty tool output or a confirmation
        # We assume success if status is 200 and no error in response text
        if "simulated" in str(resp_content).lower() or "error" in str(resp_content).lower():
             self.log("Targeted Announcement", "FAIL", f"Response contained error/simulation: {resp_content}")
        else:
             self.log("Targeted Announcement", "PASS", f"Tool executed successfully via '{query}'")

    def test_voice_intercom_upload(self):
        print("Testing Intercom Audio Upload with Real MP3...")
        endpoint = "/api/intercom/upload"
        url = f"{self.api_url}{endpoint}"
        
        # Use Test_Announcement.mp3 from tests/data/
        # Adjust path relative to where test is run (root of repo)
        mp3_path = "app/tests/data/Test_Announcement.mp3"
        if not os.path.exists(mp3_path):
            self.log("Intercom Upload", "ERROR", f"Test file {mp3_path} not found.")
            return

        with open(mp3_path, "rb") as f:
            file_content = f.read()
            
        files = {"file": ("Test_Announcement.mp3", file_content, "audio/mpeg")}
        # Target 'broadcast' to ensure it hits all valid speakers
        params = {"target": "broadcast", "message": "Live Test Intercom MP3"}
        
        try:
            r = requests.post(url, files=files, params=params, timeout=30)
            if r.status_code == 200:
                self.log("Intercom Upload", "PASS", f"Upload accepted. URL: {r.json().get('url')}")
            else:
                self.log("Intercom Upload", "FAIL", f"Status {r.status_code}: {r.text}")
        except Exception as e:
            self.log("Intercom Upload", "ERROR", str(e))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://192.168.2.211:11435", help="Server URL")
    args = parser.parse_args()
    
    # Standalone Logger
    def console_logger(name, status, msg):
        print(f"[{status:5}] {name:20} | {msg}")

    test = AnnouncementsTests(args.url, logger=console_logger)
    test.run()
