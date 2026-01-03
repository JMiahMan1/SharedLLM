
import time
from .base import BaseTest

class MediaTests(BaseTest):
    def run(self):
        self.test_volume()
        self.test_playback_dry_run()

    def test_volume(self):
        # 1. Volume Up (Generic) - Check if it resolves to a command
        # We use a known safe command that might not actually change physical volume if no device is active,
        # but the API should return a success attempt.
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Turn the volume up"}]}, "Media: Volume Up")
        
        # We expect either a success message or a specific failure like "No active media player found" which is also a PASS for the logic.
        if msg and ("done" in msg.lower() or "volume" in msg.lower() or "no active" in msg.lower() or "couldn't" in msg.lower()):
            self.log("Media: Volume Up", "PASS", msg[:50])
        else:
            self.log("Media: Volume Up", "FAIL", f"Unexpected response: {msg}")

    def test_playback_dry_run(self):
        # 2. Play Media (Dry Run check)
        # We ask to playsomething obscure to see if it generates a valid Intent match
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Play the song 'Test Tone' on Office Speaker"}]}, "Media: Play Intent")
        
        # It should try to execute or say it can't find the device. 
        # Crucially, it shouldn't crash or say "I don't understand".
        if msg and ("done" in msg.lower() or "playing" in msg.lower() or "could not find" in msg.lower() or "office speaker" in msg.lower()):
            self.log("Media: Play Intent", "PASS", msg[:50])
        else:
             self.log("Media: Play Intent", "FAIL", f"Response: {msg}")
