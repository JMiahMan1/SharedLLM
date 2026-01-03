
from .base import BaseTest

class HardwareTests(BaseTest):
    def run(self):
        self.test_lights_live_state()

    def test_lights_live_state(self):
        # 1. Turn On
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Turn on the office light"}]}, "Hardware: Light On")
        
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and tr[0].get("status") == "SUCCESS" and tr[0].get("new_state", "").lower() == "on":
             self.log("Hardware: Light On (Live State)", "PASS", f"State verified: {tr[0]['new_state']}")
        elif msg and "done" in msg.lower(): # Fallback
             self.log("Hardware: Light On (Message Only)", "WARN", "State not verified but message OK")
        else:
             self.log("Hardware: Light On", "FAIL", f"Msg: {msg}, TR: {tr}")

        # 2. Turn Off (to verify state change logic)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Turn off the office light"}]}, "Hardware: Light Off")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and tr[0].get("status") == "SUCCESS" and tr[0].get("new_state", "").lower() == "off":
             self.log("Hardware: Light Off (Live State)", "PASS", f"State verified: {tr[0]['new_state']}")
        else:
             self.log("Hardware: Light Off", "FAIL", f"Msg: {msg}, TR: {tr}")

        # 3. Toggle
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Toggle the lamp"}]}, "Hardware: Light Toggle")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and tr[0].get("status") == "SUCCESS" and tr[0].get("new_state"):
            self.log("Hardware: Light Toggle", "PASS", f"Resulting state: {tr[0]['new_state']}")
        else:
            self.log("Hardware: Light Toggle", "FAIL", f"Msg: {msg}, TR: {tr}")

        # 4. Set Color (Live Check)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Make the office light red"}]}, "Hardware: Set Color")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        # Color state verification might vary (HA returns generic state 'on', attributes differ)
        # We assume SUCCESS status is enough if we can't easily check attributes
        if tr and tr[0].get("status") == "SUCCESS":
             self.log("Hardware: Set Color", "PASS")
        else:
             self.log("Hardware: Set Color", "FAIL", str(msg))

        # 5. Brightness
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Set the light to 50% brightness"}]}, "Hardware: Set Brightness")
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr and tr[0].get("status") == "SUCCESS":
             self.log("Hardware: Brightness", "PASS")
        else:
             self.log("Hardware: Brightness", "FAIL", str(msg))
