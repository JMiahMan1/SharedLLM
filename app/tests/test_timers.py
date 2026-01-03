
import time
from .base import BaseTest

class TimerTests(BaseTest):
    def run(self):
        self._test_cycle()

    def _test_cycle(self):
        # 1. Add Timer
        unique_name = f"TestTimer_{int(time.time())}"
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Set a timer for 5 minutes called {unique_name}"}]}, "Timer: Add")
        
        if msg and ("set" in msg.lower() or "started" in msg.lower()) and "5 minutes" in msg.lower():
            self.log("Timer: Add", "PASS")
        else:
            self.log("Timer: Add", "FAIL", str(msg))
            return # Abort cycle if add failed

        # 2. List Timers (Wait a moment for Redis)
        time.sleep(1)
        # We assume the API returns a text list
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"List my timers"}]}, "Timer: List")
        if msg and unique_name in msg:
            self.log("Timer: List", "PASS")
        else:
            self.log("Timer: List", "FAIL", f"Timer {unique_name} not found in list: {msg}")

        # 3. Delete Timer
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Delete the timer called {unique_name}"}]}, "Timer: Delete")
        if msg and ("cancelled" in msg.lower() or "deleted" in msg.lower() or "stopped" in msg.lower()):
            self.log("Timer: Delete", "PASS")
        else:
             self.log("Timer: Delete", "FAIL", str(msg))
