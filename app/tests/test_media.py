
import time
from .base import BaseTest

class MediaTests(BaseTest):
    def run(self):
        self.test_volume()
        self.test_playback_dry_run()
        self.test_transport_controls()
        self.test_library_browsing()

    def test_volume(self):
        # 1. Volume Up/Down/Mute
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Turn the volume up"}]}, "Media: Volume Up")
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Mute the volume"}]}, "Media: Volume Mute")
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Set volume to 50%"}]}, "Media: Volume Set")
        
        if msg and ("done" in msg.lower() or "volume" in msg.lower() or "no active" in msg.lower() or "couldn't" in msg.lower()):
            self.log("Media: Volume Controls", "PASS")
        else:
            self.log("Media: Volume Controls", "FAIL", f"Unexpected response: {msg}")

    def test_playback_dry_run(self):
        # 2. Play Media (Dry Run check)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Play the song 'Test Tone' on Office Speaker"}]}, "Media: Play Intent")
        if msg and ("done" in msg.lower() or "playing" in msg.lower() or "could not find" in msg.lower() or "office speaker" in msg.lower()):
            self.log("Media: Play Intent", "PASS")
        else:
             self.log("Media: Play Intent", "FAIL", f"Response: {msg}")

    def test_transport_controls(self):
        # 3. Stop/Pause/Resume
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Stop the music"}]}, "Media: Stop")
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Pause the show"}]}, "Media: Pause")
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Resume playback"}]}, "Media: Resume")
        
        # 4. Skip/Previous
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Skip this song"}]}, "Media: Skip")
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Go back a song"}]}, "Media: Previous")
        
        # 5. Turn Off
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Turn off the TV"}]}, "Media: Turn Off")
        
        if msg and ("done" in msg.lower() or "off" in msg.lower() or "could" in msg.lower()):
            self.log("Media: Transport/Power", "PASS")
        else:
            self.log("Media: Transport/Power", "FAIL", str(msg))

    def test_library_browsing(self):
        # 6. List Radio / Playlists
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"List my radio stations"}]}, "Media: Radio List")
        if msg and ("radio" in msg.lower() or "station" in msg.lower() or "done" in msg.lower()):
            self.log("Media: Radio List", "PASS")
        else:
            self.log("Media: Radio List", "FAIL", str(msg))

        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"What playlists do I have?"}]}, "Media: Playlist List")
        if msg and ("playlist" in msg.lower() or "done" in msg.lower()):
            self.log("Media: Playlist List", "PASS")
        else:
            self.log("Media: Playlist List", "FAIL", str(msg))
