
from .base import BaseTest
import time

class ContextTests(BaseTest):
    def run(self):
        self.test_pronoun_resolution()
        self.test_follow_up_commands()
    
    def test_pronoun_resolution(self):
        # 1. Establish Contex: "Turn on the Office TV"
        self.log("Context: Setup", "INFO", "Turning on Office TV to set context")
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Turn on the Office TV", "user": "context_test"}]}, "Context: Setup")
        
        # 2. Follow-up: "Turn it off"
        # Since we use 'context_test' user, context should survive
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Turn it off", "user": "context_test"}]}, "Context: Pronoun 'it'")
        
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr:
            # Check if it targeted Office TV (or whatever resolved)
            entity = tr[0].get("entity_id", "")
            friendly = tr[0].get("friendly_name", "")
            if "office" in entity.lower() or "office" in friendly.lower():
                 self.log("Context: Pronoun Resolution", "PASS", f"Resolved 'it' to {friendly} ({entity})")
            else:
                 self.log("Context: Pronoun Resolution", "FAIL", f"Resolved to wrong entity: {entity}")
        else:
             self.log("Context: Pronoun Resolution", "FAIL", "No tool executed")

    def test_follow_up_commands(self):
        # 1. Play Music
        self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Play music on the Kitchen Speaker", "user": "context_test_2"}]}, "Context: Setup 2")
        
        # 2. "Volume up" (Should target Kitchen Speaker implicitly)
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":"Volume up", "user": "context_test_2"}]}, "Context: Implicit Entity")
        
        tr = self.last_response_json.get("tool_results", []) if self.last_response_json else []
        if tr:
             entity = tr[0].get("entity_id", "")
             if "kitchen" in entity.lower():
                 self.log("Context: Implicit Entity", "PASS", f"Volume Up targeted {entity}")
             else:
                 self.log("Context: Implicit Entity", "FAIL", f"Targeted: {entity}")
        else:
             self.log("Context: Implicit Entity", "FAIL", "No tool executed")
