
import time
from .base import BaseTest

class AdvancedTests(BaseTest):
    def run(self):
        self.test_compound_commands()
        self.test_conversation_context()
        self.test_ha_notifications()

    def test_compound_commands(self):
        # 1. Compound Command Splitting
        unique_timer = f"AdvTimer_{int(time.time())}"
        query = f"Turn on the office light and set a timer for 10 minutes called {unique_timer}"
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":query}]}, "Advanced: Compound Commands")
        
        # We expect a compound response or multiple tool executions (Silent Mode might just say "Done.")
        if msg and ("done" in msg.lower() or "timer" in msg.lower() or "light" in msg.lower()):
            self.log("Advanced: Compound Commands", "PASS")
        else:
            self.log("Advanced: Compound Commands", "FAIL", str(msg))
            
        # Cleanup timer
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Delete the timer {unique_timer}"}]}, "Advanced: Cleanup Timer")

    def test_conversation_context(self):
        # 2. History / Contextualization
        # Step A: Establish context
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"What is 5 plus 5?"}]}, "Advanced: Context (Setup)")
        
        # Step B: Follow-up
        time.sleep(1)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Multiply that by 3"}]}, "Advanced: Context (Follow-up)")
        
        if msg and ("30" in msg or "thirty" in msg.lower()):
            self.log("Advanced: Conversation Context", "PASS")
        else:
            self.log("Advanced: Conversation Context", "FAIL", f"Expected 30, got: {msg}")

    def test_ha_notifications(self):
        # 3. HA Notifications tool
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Send a notification saying the test is complete"}]}, "Advanced: HA Notification")
        
        if msg and ("done" in msg.lower() or "notification" in msg.lower() or "sent" in msg.lower()):
            self.log("Advanced: HA Notification", "PASS")
        else:
            self.log("Advanced: HA Notification", "FAIL", str(msg))
