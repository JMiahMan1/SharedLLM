
import asyncio
from typing import Dict, Any

# Mock class to bypass imports
class MediaIntegration:
    pass

# Simplified copy of the class to test logic in isolation if imports fail, 
# but better to try importing the real one first.
try:
    from app.domains.media.integrations.music_assistant import MusicAssistantIntegration
except ImportError:
    print("Could not import real integration, using mock logic for testing...")
    import re
    class MusicAssistantIntegration:
        def _clean_query(self, query: str, device_name: str = "") -> str:
            """MA specific cleaner (Copied from source)."""
            # 1. Normalize: Lowercase and strip apostrophes early to match entity names (e.g. Gracie's -> gracies)
            clean = query.lower().replace("'", "").replace("’", "")
            
            # 2. Remove device name if known
            if device_name:
                # Normalize device name too to match the query
                d_clean = device_name.lower().replace("'", "").replace("’", "").strip()
                # Try to remove "on [device_name]" first
                clean = re.sub(r"\b(on|in|at|to|from)\b\s+(the\s+)?" + re.escape(d_clean) + r"\b", " ", clean)
                # Remove just the device name
                clean = clean.replace(d_clean, " ")
                
                # Fuzzy
                clean = self._fuzzy_remove_device(clean, d_clean)
            
            # 3. Remove common MA keywords
            clean = re.sub(r"\b(music|song|album|track|playlist|artist|radio|podcast)\b", " ", clean)
            # 4. Remove actions
            clean = re.sub(r"\b(play|please|from|on|open|launch|playback|listen to)\b", " ", clean)
            
            # 5. Remove "the" if standalone
            clean = re.sub(r"\bthe\b", "", clean)
            
            # 6. Remove remaining punctuation
            clean = re.sub(r"[^\w\s]", "", clean)
            
            return re.sub(r'\s+', ' ', clean).strip()

        def _fuzzy_remove_device(self, query: str, device_name: str) -> str:
            import difflib
            q_tokens = query.split()
            d_tokens = device_name.split()
            if not d_tokens or not q_tokens: return query
            n = len(d_tokens)
            if len(q_tokens) >= n:
                suffix = " ".join(q_tokens[-n:])
                ratio = difflib.SequenceMatcher(None, suffix, device_name).ratio()
                if ratio > 0.8: return " ".join(q_tokens[:-n])
            if len(q_tokens) >= n + 1:
                suffix = " ".join(q_tokens[-(n+1):])
                ratio = difflib.SequenceMatcher(None, suffix, device_name).ratio()
                if ratio > 0.8: return " ".join(q_tokens[:-(n+1)])
            return query

async def test_cleaning():
    ma = MusicAssistantIntegration()
    
    # Scenario 1: Canonical name matches spoken name (Ideal)
    # Metadata name: "Gracies TV", Query: "Play Brandon Lake on Gracies TV"
    q1 = "Play Brandon Lake on Gracies TV"
    d1 = "Gracies TV"
    c1 = ma._clean_query(q1, d1)
    print(f"Scenario 1 (Match): '{q1}' - '{d1}' => '{c1}'")
    
    # Scenario 2: Canonical name has apostrophe (Real world)
    # Metadata name: "Gracie's TV", Query: "Play Brandon Lake on Gracies TV"
    q2 = "Play Brandon Lake on Gracies TV"
    d2 = "Gracie's TV" 
    c2 = ma._clean_query(q2, d2)
    print(f"Scenario 2 (Apostrophe): '{q2}' - '{d2}' => '{c2}'")

    # Scenario 3: Canonical name has underscore (System name)
    # Metadata name: "Gracies_TV", Query: "Play Brandon Lake on Gracies TV"
    q3 = "Play Brandon Lake on Gracies TV"
    d3 = "Gracies_TV"
    c3 = ma._clean_query(q3, d3)
    print(f"Scenario 3 (Underscore): '{q3}' - '{d3}' => '{c3}'")

    # Scenario 4: Missing Device Name (The Regression)
    # Metadata name: None (or not passed), Query: "Play Brandon Lake on Gracies TV"
    q4 = "Play Brandon Lake on Gracies TV"
    d4 = "" # simulate missing arg
    c4 = ma._clean_query(q4, d4)
    print(f"Scenario 4 (Missing): '{q4}' - '{d4}' => '{c4}'")


if __name__ == "__main__":
    asyncio.run(test_cleaning())
