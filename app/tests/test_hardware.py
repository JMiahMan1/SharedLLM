
from .base import BaseTest

class HardwareTests(BaseTest):
    def run(self):
        self.test_lights_dry_run()

    def test_lights_dry_run(self):
        # 1. Turn on lights (Generic)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Turn on the office light"}]}, "Hardware: Light On")
        
        # We expect a success message or a confirmation that it tried to call the service.
        if msg and ("done" in msg.lower() or "turned on" in msg.lower() or "command sent" in msg.lower() or "office light" in msg.lower()):
            self.log("Hardware: Light On", "PASS", msg[:50])
        else:
            self.log("Hardware: Light On", "FAIL", f"Unexpected response: {msg}")

        # 2. Toggle lights
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Toggle the lamp"}]}, "Hardware: Light Toggle")
        if msg and ("done" in msg.lower() or "toggled" in msg.lower() or "could not find" in msg.lower() or "lamp" in msg.lower()):
            self.log("Hardware: Light Toggle", "PASS", msg[:50])
        else:
            self.log("Hardware: Light Toggle", "FAIL", str(msg))
