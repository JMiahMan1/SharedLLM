
import time
from .base import BaseTest

class ProductivityTests(BaseTest):
    def run(self):
        self.test_calendar_cycle()
        self.test_notes_cycle()

    def test_calendar_cycle(self):
        title = f"TestEvent_{int(time.time())}"
        
        # Add
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Schedule {title} for tomorrow at 10am"}]}, "Calendar: Add")
        if msg and "Scheduled" in msg:
            self.log("Calendar: Add", "PASS")
        else:
            self.log("Calendar: Add", "FAIL", str(msg)[:100])
            return

        # List/Verify
        time.sleep(1)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"What's on my calendar for tomorrow?"}]}, "Calendar: List")
        if msg and title in msg:
            self.log("Calendar: List", "PASS")
        else:
            self.log("Calendar: List", "FAIL", f"Event {title} not found in response")

        # Delete
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Cancel the event {title}"}]}, "Calendar: Delete")
        if msg and ("deleted" in msg.lower() or "cancelled" in msg.lower() or "removed" in msg.lower()):
            self.log("Calendar: Delete", "PASS")
        else:
            self.log("Calendar: Delete", "FAIL", str(msg)[:100])

    def test_notes_cycle(self):
        title = f"TestNote_{int(time.time())}"
        # Create
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Create note {title} with content 'Automated test content'"}]}, "Note: Create")
        if msg and ("success" in msg.lower() or "created" in msg.lower()):
            self.log("Note: Create", "PASS")
        else:
            self.log("Note: Create", "FAIL", str(msg)[:100])
            return
        
        # Read
        time.sleep(1)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Read the note {title}"}]}, "Note: Read")
        if msg and "Automated test content" in msg:
            self.log("Note: Read", "PASS")
        else:
            self.log("Note: Read", "FAIL", f"Content mismatch or note not found. Got: {msg[:100]}")
            
        # Delete
        time.sleep(1)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Delete the note {title}"}]}, "Note: Delete")
        if msg and "deleted" in msg.lower():
            self.log("Note: Delete", "PASS")
        else:
            self.log("Note: Delete", "FAIL", str(msg)[:100])
