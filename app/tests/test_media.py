
import time
from .base import BaseTest

class MediaTests(BaseTest):
    def run(self):
        self.test_playback_dry_run() # Start playback first so volume/transport controls work
        self.test_watch_intent() # Verify Video/Watch Intent specifically
        self.test_volume()
        self.test_transport_controls()
        self.test_library_browsing()

    def test_volume(self):
        # 0. Initial set to safe level (40%)
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Set volume to 40% on the Office TV"}]}, "Media: Volume Init")
        
        # 1. Volume Up/Down/Mute
        # Volume Up from 40% should be around 50%, safe within 65% limit
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

        # Set to 50% (between 20% and 65%)
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
        entity = "media_player.office_tv_chrome_2"

        # STOP
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Stop the music on the Office TV"}]}, "Media: Stop")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and tr[0].get("status") == "SUCCESS":
             # Verify state becomes idle or off
             state = None
             for _ in range(5):
                 state = self.get_entity_state(entity)
                 if state in ["idle", "off", "paused"]: 
                     break
                 time.sleep(1)
             if state in ["idle", "off", "paused"]:
                 self.log("Media: Stop", "PASS", f"State: {state}")
             else:
                 self.log("Media: Stop", "WARN", f"Command SUCCESS but state is {state}")
        else:
             self.log("Media: Stop", "FAIL", f"TR: {tr}")

        # Pause
        # Need to ensure something is playing first? We might have stopped it above.
        # Let's skip strict state check for Pause if we just stopped it, OR run play first.
        # For robustness, we'll try to Play then Pause.
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Play Tim Timmons on Office TV"}]}, "Media: Play (Pre-Pause)")
        time.sleep(10) # Buffer for title to appear (increased from 5s)
        
        # Capture title before pause
        data_before = self.get_entity_full(entity)
        title_before = data_before.get("attributes", {}).get("media_title")

        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Pause the show on the Office TV"}]}, "Media: Pause")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and tr[0].get("status") == "SUCCESS":
             state = None
             for _ in range(5):
                 state = self.get_entity_state(entity)
                 if state == "paused":
                     break
                 time.sleep(1)
             if state == "paused":
                 self.log("Media: Pause", "PASS", f"State: {state} | Title: {title_before}")
             else:
                 self.log("Media: Pause", "WARN", f"State: {state}")
        else:
             self.log("Media: Pause", "FAIL", f"TR: {tr}")

        # Resume
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Resume playback on the Office TV"}]}, "Media: Resume")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and (tr[0].get("status") == "SUCCESS" or "no active" in tr[0].get("message", "").lower()):
              state = None
              title_after = None
              for _ in range(5):
                 data_after = self.get_entity_full(entity)
                 state = data_after.get("state")
                 title_after = data_after.get("attributes", {}).get("media_title")
                 if state in ["playing", "buffering"]:
                     break
                 time.sleep(1)
              
              if state in ["playing", "buffering"]:
                  if title_before == title_after:
                      self.log("Media: Resume", "PASS", f"State: {state} | Title Confirmed: {title_after}")
                  else:
                      self.log("Media: Resume", "WARN", f"State: {state} | Title Mismatch: {title_before} vs {title_after}")
              else:
                   self.log("Media: Resume", "WARN", f"State: {state}")
        else:
             self.log("Media: Resume", "FAIL", f"TR: {tr}")
        
        # 4. Skip/Previous
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Skip this song"}]}, "Media: Skip")
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Go back a song"}]}, "Media: Previous")
        
        # 5. Turn Off
        entity = "media_player.office_tv" # Target the hardware entity for power
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Turn off the Office TV"}]}, "Media: Turn Off")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        
        if tr and tr[0].get("status") == "SUCCESS":
            # State Verification
            state = None
            for _ in range(10): # Give it time to update state
                state = self.get_entity_state(entity)
                if state in ["off", "standby", "unavailable"]:
                    break
                time.sleep(1)
            
            if state in ["off", "standby", "unavailable"]:
                self.log("Media: Transport/Power", "PASS", f"Final State: {state}")
            else:
                self.log("Media: Transport/Power", "FAIL", f"Command SUCCESS but state is {state}")
        else:
            self.log("Media: Transport/Power", "FAIL", f"Command failed: {msg}")

    def test_watch_intent(self):
        # 1. Watch Video
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Watch the video Big Buck Bunny on Office TV"}]}, "Media: Watch Video")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        
        if tr and tr[0].get("status") == "SUCCESS":
             # Verify state becomes playing
             entity = "media_player.office_tv_chrome_2"
             state = None
             title = None
             for i in range(15): # Give 15 seconds for YouTube app to launch and start
                 data = self.get_entity_full(entity)
                 state = data.get("state")
                 title = data.get("attributes", {}).get("media_title")
                 if state in ["playing", "buffering"]: 
                     break
                 time.sleep(1)
                 
             if state in ["playing", "buffering"]:
                 self.log("Media: Watch Video", "PASS", f"State: {state} | Title: {title}")
             else:
                 self.log("Media: Watch Video", "FAIL", f"Command SUCCESS but state is {state} (Timeout)")
        else:
             self.log("Media: Watch Video", "FAIL", f"TR: {tr}")

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
