
import os
import requests
import io
import time
from app.tests.base import BaseTest

class AnnouncementsTests(BaseTest):
    def run(self):
        print("\n[AnnouncementsTests] Starting Live Tests...")
        self.test_text_announcement()
        self.test_voice_intercom_upload()
        print("[AnnouncementsTests] Finished.\n")

    def test_text_announcement(self):
        query = "Announce Testing live text announcement 🔔"
        print(f"Testing Query: '{query}'")
        
        # We use the raw chat endpoint via safe_post
        payload = {
            "model": "qwen3:14b", # or default
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

    def test_voice_intercom_upload(self):
        print("Testing Intercom Audio Upload...")
        endpoint = "/api/intercom/upload"
        url = f"{self.api_url}{endpoint}"
        
        # Create dummy WAV content (header only or minimal silence)
        # Minimal valid WAV header (44 bytes) for 16-bit PCM, Mono, 44100Hz
        # verifying if server strictly checks validity or just extension. 
        # Using a minimal valid structure to be safe.
        wav_header = b'RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00'
        
        file_obj = io.BytesIO(wav_header)
        file_obj.name = "test_intercom.wav"
        
        files = {"file": ("test_intercom.wav", file_obj, "audio/wav")}
        params = {"target": "broadcast", "message": "Live Test Intercom"}
        
        try:
            # Using raw requests because BaseTest.safe_post sends JSON
            r = requests.post(url, files=files, params=params, timeout=10)
            if r.status_code == 200:
                self.log("Intercom Upload", "PASS", "Upload accepted")
                # Cannot easily verify playback without hearing it, but 200 OK means logic ran.
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
