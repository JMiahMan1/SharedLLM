
import asyncio
import sys
import os
from unittest.mock import MagicMock

# Path hack
sys.path.append(sys.path[0] + "/app")

from logic import pipeline
from logic.utils import update_history
from settings import DEFAULT_MODEL

# Mock Action Dispatcher to intercept commands
async def mock_dispatch(tool_name, query, user_creds, model, params):
    print(f"[MOCK DISPATCH] Tool: {tool_name}, Params: {params}")
    
    # State tracking simulation
    intent = params.get("intent")
    device = params.get("device_name", "")
    
    if tool_name == "media_command":
        if intent in ["play_media", "play"]:
             # Simulate Device Off condition for "Office TV"
             if "office tv" in str(device).lower() or "office tv" in query.lower():
                 return {"status": "FAILURE", "message": "Device 'Office TV' is currently powered off.", "service": "media_player"}
             return {"status": "SUCCESS", "message": "Playing music.", "service": "media_player"}
        
        if intent == "turn_on":
             return {"status": "SUCCESS", "message": "Office TV turned on.", "service": "media_player"}

    return {"status": "SUCCESS", "message": "Done."}

# Patch Dispatcher
pipeline.ActionDispatcher.dispatch = mock_dispatch

# Mock get_history_context
def mock_get_history(user):
    return "User: Play music on Office TV\nAssistant: The Office TV is currently powered off. Would you like me to turn it on first so we can play music?"

pipeline.get_history_context = mock_get_history

async def run_device_context_test():
    print("--- Starting Device Context Test ---")
    user = "test_user_device_context"
    
    # Turn 1: Intent to Play
    q1 = "Play music on Office TV"
    print(f"\nUser: {q1}")
    
    gen1 = pipeline.generate_rag_stream(q1, user, DEFAULT_MODEL, False, "chat")
    ans1 = ""
    async for chunk in gen1:
        try:
            import json
            if chunk.strip() and not chunk.startswith("data: [DONE]"):
                data = json.loads(chunk.replace("data: ", ""))
                if "message" in data: ans1 += data["message"].get("content", "")
                elif "choices" in data: ans1 += data["choices"][0]["delta"].get("content", "")
        except: pass
    
    print(f"Assistant: {ans1}", flush=True)
    
    # Check if Assistant noticed the failure and asked for confirmation
    if "off" not in ans1.lower():
        print("⚠️ Warning: Assistant did not explicitly mention device is off. This might be due to LLM variation.", flush=True)
        
    # Turn 2: "Yes"
    q2 = "Yes"
    print(f"\nUser: {q2}", flush=True)
    
    # Capture the internal thoughts (Re-written Query) if possible?
    # We can't easily see the rewritten query without logging, 
    # BUT we can see the MOCK DISPATCH calls.
    # We expect:
    # 1. Turn On Office TV
    # 2. Play Music on Office TV (The crucial part!)
    
    gen2 = pipeline.generate_rag_stream(q2, user, DEFAULT_MODEL, False, "chat")
    ans2 = ""
    async for chunk in gen2:
        try:
             import json
             if chunk.strip() and not chunk.startswith("data: [DONE]"):
                data = json.loads(chunk.replace("data: ", ""))
                if "message" in data: ans2 += data["message"].get("content", "")
                elif "choices" in data: ans2 += data["choices"][0]["delta"].get("content", "")
        except: pass
        
    print(f"Assistant: {ans2}")

if __name__ == "__main__":
    asyncio.run(run_device_context_test())
