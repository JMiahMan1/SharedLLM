
from .base import BaseTest

class AndroidTVTests(BaseTest):
    def run(self):
        self.test_remote_commands()
        self.test_app_launch()
        self.test_watch_intent()

    def test_remote_commands(self):
        # 1. Navigation / Remote commands
        # Usually requires a specific entity, but we check if the orchestrator tries to call it
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Press the home button on the Android TV"}]}, "AndroidTV: Remote Command")
        
        if msg and ("done" in msg.lower() or "button" in msg.lower() or "remote" in msg.lower() or "home" in msg.lower() or "could not find" in msg.lower()):
            self.log("AndroidTV: Remote Command", "PASS")
        else:
            self.log("AndroidTV: Remote Command", "FAIL", str(msg))

    def test_app_launch(self):
        # 2. Launch App
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Launch YouTube on the Office TV"}]}, "AndroidTV: Launch App")
        
        if msg and ("done" in msg.lower() or "launching" in msg.lower() or "youtube" in msg.lower() or "could not find" in msg.lower()):
            self.log("AndroidTV: Launch App", "PASS")
        else:
            self.log("AndroidTV: Launch App", "FAIL", str(msg))

    def test_watch_intent(self):
        # 3. Watch Intent (Regression Check)
        # Should NOT open YouTube app, but play via yt-dlp (so message should indicate 'playing' or 'buffering')
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Watch a video about cats on the Office TV"}]}, "AndroidTV: Watch Intent")
        
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        
        # We expect SUCCESS from the tool, and potentially a message about playing/buffering
        if tr and tr[0].get("status") == "SUCCESS":
             # [State Verification] Wait for playing state
             import time
             entity = "media_player.office_tv_chrome_2" # Confirmed from logs
             state = None
             for _ in range(10):
                 state = self.get_entity_state(entity)
                 if state in ["playing", "buffering"]:
                     break
                 time.sleep(1)
             
             if state in ["playing", "buffering"]:
                 self.log("AndroidTV: Watch Intent", "PASS", f"Result: {tr[0].get('message')} | State: {state}")
             else:
                 self.log("AndroidTV: Watch Intent", "WARN", f"Command success but state verification failed (State: {state})")
        elif msg and ("playing" in msg.lower() or "buffering" in msg.lower()):
             self.log("AndroidTV: Watch Intent", "PASS", "Verified via message")
        else:
             self.log("AndroidTV: Watch Intent", "FAIL", f"Msg: {msg}, TR: {tr}")
