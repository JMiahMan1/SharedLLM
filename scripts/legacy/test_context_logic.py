
import asyncio
import sys
import os
from unittest.mock import MagicMock, patch

# Path hack
sys.path.append(sys.path[0] + "/app")

# Mock dependencies to avoid full app spinup
from logic import pipeline
from logic.utils import update_history, get_history_context
from settings import GlobalResources, DEFAULT_MODEL

# Mock Web Search to avoid 503s
async def mock_web_search(query):
    print(f"[MOCK SEARCH] Searching for: {query}")
    return "### Search Result:\nTitle: Prime Minister of Canada\nURL: https://pm.gc.ca\nSnippet: Justin Trudeau is the 23rd Prime Minister of Canada."

# Patch the tool in pipeline
pipeline.tool_web_search = mock_web_search

async def run_context_test():
    print("--- Starting Context Logic Test ---")
    user = "test_user_context"
    
    # Clear history (if any) - assuming sqlite or simple list
    # Actually we can't easily clear the real DB without `history.py` access.
    # We'll just define a new user to ensure clean state.
    
    # 1. Turn 1
    q1 = "Who is the Prime Minister of Canada?"
    print(f"\nUser: {q1}")
    
    # Run Pipeline
    gen1 = pipeline.generate_rag_stream(q1, user, DEFAULT_MODEL, False, "chat")
    ans1 = ""
    async for chunk in gen1:
        # Parse NDJSON
        try:
            import json
            if chunk.strip():
                data = json.loads(chunk)
                if "message" in data:
                    ans1 += data["message"].get("content", "")
        except: pass
    
    print(f"Assistant: {ans1}")
    
    if "Trudeau" in ans1 or "Justin" in ans1:
        print("✅ Turn 1 Response checks out.")
    else:
        print(f"❌ Turn 1 Response missing expected keywords. Got: {ans1}")

    # 2. Turn 2 (Context Dependent)
    q2 = "How old is he?"
    print(f"\nUser: {q2}")
    
    # We want to enable debug logging to see the REWRITTEN query
    # But checking the result might be enough. 
    # If the LLM answers "Justin Trudeau is 52..." then it worked.
    # If it asks "Who is he?", it failed.
    
    gen2 = pipeline.generate_rag_stream(q2, user, DEFAULT_MODEL, False, "chat")
    ans2 = ""
    async for chunk in gen2:
        try:
             import json
             if chunk.strip():
                data = json.loads(chunk)
                if "message" in data:
                    ans2 += data["message"].get("content", "")
        except: pass
        
    print(f"Assistant: {ans2}")
    
    # Verification
    if "Trudeau" in ans2 or "Justin" in ans2 or "52" in ans2 or "53" in ans2:
        print("✅ Turn 2 Context Success (Entity maintained).")
    elif "who" in ans2.lower():
        print("❌ Turn 2 Context Failed (LLM asked for clarification).")
    else:
        print("⚠️ Turn 2 Ambiguous.")

if __name__ == "__main__":
    asyncio.run(run_context_test())
