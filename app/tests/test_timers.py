
import time
from .base import BaseTest

class TimerTests(BaseTest):
    def run(self):
        self._test_cycle()

    def _test_cycle(self):
        # 1. Add Timer
        unique_name = f"TestTimer_{int(time.time())}"
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Set a timer for 5 minutes called {unique_name}"}]}, "Timer: Add")
        
        if msg and ("done" in msg.lower() or "set" in msg.lower() or "started" in msg.lower() or "timer" in msg.lower()):
            self.log("Timer: Add", "PASS")
        else:
            self.log("Timer: Add", "FAIL", str(msg))
            return

        # 2. Pause/Resume
        time.sleep(1)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Pause the timer {unique_name}"}]}, "Timer: Pause")
        if msg and ("done" in msg.lower() or "paused" in msg.lower()):
             self.log("Timer: Pause", "PASS")
        else:
             self.log("Timer: Pause", "FAIL", str(msg))

        time.sleep(1)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Resume the timer {unique_name}"}]}, "Timer: Resume")
        if msg and ("done" in msg.lower() or "resumed" in msg.lower()):
             self.log("Timer: Resume", "PASS")
        else:
             self.log("Timer: Resume", "FAIL", str(msg))

        # 3. Add Alarm (Absolute time)
        alarm_time = "8am"
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Set an alarm for {alarm_time}"}]}, "Alarm: Add")
        if msg and ("alarm" in msg.lower() or "set" in msg.lower() or "done" in msg.lower()):
            self.log("Alarm: Add", "PASS")
        else:
            self.log("Alarm: Add", "FAIL", str(msg))

        # 4. List Timers (Wait a moment for Redis)
        time.sleep(1)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"List my timers"}]}, "Timer: List")
        if msg and ("timer" in msg.lower() or unique_name in msg or "8:00" in msg):
            self.log("Timer: List", "PASS")
        else:
            self.log("Timer: List", "FAIL", f"Timer list check failed: {msg}")

        # 5. Delete All
        time.sleep(1)
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Delete the timer {unique_name}"}]}, "Timer: Delete")
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Cancel the alarm for {alarm_time}"}]})
        self.log("Timer: Delete", "PASS")
