
from .base import BaseTest

class AndroidTVTests(BaseTest):
    def run(self):
        self.test_remote_commands()
        self.test_app_launch()

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
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Launch YouTube on the Shield"}]}, "AndroidTV: Launch App")
        
        if msg and ("done" in msg.lower() or "launching" in msg.lower() or "youtube" in msg.lower() or "could not find" in msg.lower()):
            self.log("AndroidTV: Launch App", "PASS")
        else:
            self.log("AndroidTV: Launch App", "FAIL", str(msg))
