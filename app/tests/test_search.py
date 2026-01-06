
from .base import BaseTest

class SearchTests(BaseTest):
    def run(self):
        self._test_web_search()
        self._test_music_search()

    def _test_web_search(self):
        # Use a query that definitely requires external knowledge and is time-variant or specific
        query = "What is the capital of Mongolia?"
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":query}]}, "Search: Web Query")
        
        if msg and "Ulaanbaatar" in msg:
            self.log("Search: Web Query", "PASS")
        else:
            self.log("Search: Web Query", "FAIL", f"Expected 'Ulaanbaatar', got: {msg[:100]}")

    def _test_music_search(self):
        query = "Who is the artist Brandon Lake?"
        msg, status = self.safe_post("/api/chat", {"messages":[{"role":"user","content":query}]}, "Search: Music/Artist")
        
        if msg and ("brandon lake" in msg.lower() or "singer" in msg.lower() or "artist" in msg.lower()):
            self.log("Search: Music/Artist", "PASS")
        else:
            self.log("Search: Music/Artist", "FAIL", f"Response: {msg[:100]}")
