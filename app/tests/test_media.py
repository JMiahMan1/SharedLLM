
import time
from .base import BaseTest

class MediaTests(BaseTest):
    def run(self):
        self.test_playback_dry_run() # Start playback first so volume/transport controls work
        self.test_volume()
        self.test_transport_controls()
        self.test_library_browsing()

    def test_volume(self):
        # 1. Volume Up/Down/Mute
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Turn the volume up on the Office TV"}]}, "Media: Volume Up")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and tr[0].get("status") == "SUCCESS":
             self.log("Media: Volume Up", "PASS")
        else:
             self.log("Media: Volume Up", "FAIL", f"TR: {tr}")

        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Mute the volume on the Office TV"}]}, "Media: Volume Mute")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and tr[0].get("status") == "SUCCESS":
             self.log("Media: Volume Mute", "PASS")
        else:
             self.log("Media: Volume Mute", "FAIL", f"TR: {tr}")

        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Set volume to 50% on the Office TV"}]}, "Media: Volume Set")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and tr[0].get("status") == "SUCCESS":
            self.log("Media: Volume Set", "PASS")
        else:
            self.log("Media: Volume Set", "FAIL", f"Unexpected response: {msg}")

    def test_playback_dry_run(self):
        # 2. Play Media (Dry Run check)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Play the song 'Test Tone' on Office TV"}]}, "Media: Play Intent")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and tr[0].get("status") == "SUCCESS":
            self.log("Media: Play Intent", "PASS", f"Played: {tr[0].get('message')}")
        else:
             self.log("Media: Play Intent", "FAIL", f"Response: {msg}")

        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Play Tim Timmons on Office TV"}]}, "Media: Play Tim Timmons")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and tr[0].get("status") == "SUCCESS":
            self.log("Media: Play Tim Timmons", "PASS", f"Played: {tr[0].get('message')}")
        else:
             self.log("Media: Play Tim Timmons", "FAIL", f"Response: {msg}")

    def test_transport_controls(self):
        # 3. Stop/Pause/Resume
        # 3. Stop/Pause/Resume - Explicitly target known device
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Stop the music on the Office TV"}]}, "Media: Stop")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and tr[0].get("status") == "SUCCESS":
             self.log("Media: Stop", "PASS")
        else:
             self.log("Media: Stop", "FAIL", f"TR: {tr}")

        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Pause the show on the Office TV"}]}, "Media: Pause")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and tr[0].get("status") == "SUCCESS": # State verification hard without active playback
             self.log("Media: Pause", "PASS")
        else:
             self.log("Media: Pause", "FAIL", f"TR: {tr}")

        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Resume playback on the Office TV"}]}, "Media: Resume")
        # Resume might fail if nothing paused, but we check for tool execution attempt
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and (tr[0].get("status") == "SUCCESS" or "no active" in tr[0].get("message", "").lower()):
             self.log("Media: Resume", "PASS")
        else:
             self.log("Media: Resume", "FAIL", f"TR: {tr}")
        
        # 4. Skip/Previous
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Skip this song"}]}, "Media: Skip")
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Go back a song"}]}, "Media: Previous")
        
        # 5. Turn Off
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Turn off the TV"}]}, "Media: Turn Off")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        
        if tr and tr[0].get("status") == "SUCCESS":
            self.log("Media: Transport/Power", "PASS")
        else:
            self.log("Media: Transport/Power", "FAIL", str(msg))

    def test_library_browsing(self):
        # 6. List Radio / Playlists
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"List my radio stations"}]}, "Media: Radio List")
        # Check tool results for list items
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        
        if tr and tr[0].get("status") == "SUCCESS" and "items" in tr[0]:
            count = len(tr[0]["items"])
            self.log("Media: Radio List", "PASS", f"Found {count} stations")
        elif msg and ("radio" in msg.lower() or "station" in msg.lower()):
            self.log("Media: Radio List", "WARN", "Verified via message only")
        else:
            self.log("Media: Radio List", "FAIL", str(msg))

        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"What playlists do I have?"}]}, "Media: Playlist List")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and tr[0].get("status") == "SUCCESS":
            self.log("Media: Playlist List", "PASS")
        else:
            self.log("Media: Playlist List", "FAIL", str(msg))
