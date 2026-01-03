
from .base import BaseTest

class HardwareTests(BaseTest):
    def run(self):
        self.test_lights_dry_run()

    def test_lights_dry_run(self):
        # 1. Turn on/off (Generic)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Turn on the office light"}]}, "Hardware: Light On")
        if msg and ("done" in msg.lower() or "turned on" in msg.lower() or "office light" in msg.lower()):
            self.log("Hardware: Light On", "PASS")
        else:
            self.log("Hardware: Light On", "FAIL", f"Unexpected response: {msg}")

        # 2. Toggle lights
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Toggle the lamp"}]}, "Hardware: Light Toggle")
        if msg and ("done" in msg.lower() or "toggled" in msg.lower() or "lamp" in msg.lower()):
            self.log("Hardware: Light Toggle", "PASS")
        else:
            self.log("Hardware: Light Toggle", "FAIL", str(msg))

        # 3. Set Color
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Make the office light red"}]}, "Hardware: Set Color")
        if msg and ("done" in msg.lower() or "red" in msg.lower() or "color" in msg.lower()):
            self.log("Hardware: Set Color", "PASS")
        else:
            self.log("Hardware: Set Color", "FAIL", str(msg))

        # 4. Brightness
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Set the light to 50% brightness"}]}, "Hardware: Set Brightness")
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Dim the lights"}]}, "Hardware: Dim")
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Brighten the lamp"}]}, "Hardware: Brighten")
        if msg and ("done" in msg.lower() or "brightness" in msg.lower() or "bright" in msg.lower() or "dim" in msg.lower()):
            self.log("Hardware: Brightness", "PASS")
        else:
            self.log("Hardware: Brightness", "FAIL", str(msg))
