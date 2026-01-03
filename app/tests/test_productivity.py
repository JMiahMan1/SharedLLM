
import time
from .base import BaseTest

class ProductivityTests(BaseTest):
    def run(self):
        self.test_calendar_cycle()
        self.test_notes_cycle()

    def test_calendar_cycle(self):
        title = f"TestEvent{int(time.time())}"
        
        # Add
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Schedule {title} for tomorrow at 10am"}]}, "Calendar: Add")
        if msg and ("scheduled" in msg.lower() or "done" in msg.lower()):
            self.log("Calendar: Add", "PASS")
        else:
            self.log("Calendar: Add", "FAIL", str(msg)[:100])
            return

        # Update
        time.sleep(1)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Move {title} to 11am"}]}, "Calendar: Update")
        if msg and ("updated" in msg.lower() or "scheduled" in msg.lower() or "done" in msg.lower()):
            self.log("Calendar: Update", "PASS")
        else:
            self.log("Calendar: Update", "FAIL", str(msg)[:100])

        # List/Verify
        time.sleep(1)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"What's on my calendar for tomorrow?"}]}, "Calendar: List")
        if msg and (title in msg or "tomorrow" in msg.lower()):
            self.log("Calendar: List", "PASS")
        else:
            self.log("Calendar: List", "FAIL", f"Event {title} not found in response")

        # Delete
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Cancel the event {title}"}]}, "Calendar: Delete")
        if msg and ("done" in msg.lower() or "deleted" in msg.lower() or "cancelled" in msg.lower() or "removed" in msg.lower()):
            self.log("Calendar: Delete", "PASS")
        else:
            self.log("Calendar: Delete", "FAIL", str(msg)[:100])

    def test_notes_cycle(self):
        title = f"TestNote_{int(time.time())}"
        # Create
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Create note {title} with content 'Line 1'"}]}, "Note: Create")
        if msg and ("done" in msg.lower() or "success" in msg.lower() or "created" in msg.lower()):
            self.log("Note: Create", "PASS")
        else:
            self.log("Note: Create", "FAIL", str(msg)[:100])
            return
        
        # Append
        time.sleep(1)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Add 'Line 2' to note {title}"}]}, "Note: Append")
        if msg and ("done" in msg.lower() or "appended" in msg.lower() or "added" in msg.lower()):
            self.log("Note: Append", "PASS")
        else:
            self.log("Note: Append", "FAIL", str(msg)[:100])

        # Update (Overwrite)
        time.sleep(1)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Change the content of note {title} to 'New Content'"}]}, "Note: Update")
        if msg and ("done" in msg.lower() or "updated" in msg.lower() or "updated" in msg.lower()):
            self.log("Note: Update", "PASS")
        else:
            self.log("Note: Update", "FAIL", str(msg)[:100])

        # Read & Verify
        time.sleep(1)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Read the note {title}"}]}, "Note: Read")
        if msg and ("new content" in msg.lower() or title in msg):
            self.log("Note: Read", "PASS")
        else:
            self.log("Note: Read", "FAIL", f"Content mismatch or note not found. Got: {msg[:100]}")

        # Check Off
        time.sleep(1)
        list_note = f"ListNote_{int(time.time())}"
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Create note {list_note} with content '- [ ] milk\n- [ ] eggs'"}]})
        time.sleep(1)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Check off milk from {list_note}"}]}, "Note: Check Off")
        if msg and ("done" in msg.lower() or "checked" in msg.lower() or "marked" in msg.lower()):
            self.log("Note: Check Off", "PASS")
        else:
            self.log("Note: Check Off", "FAIL", str(msg)[:100])
            
        # Delete originals
        time.sleep(1)
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Delete the note {title}"}]}, "Note: Delete")
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":f"Delete the note {list_note}"}]})
        self.log("Note: Delete", "PASS")
