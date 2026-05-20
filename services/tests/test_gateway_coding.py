import os
import httpx
import time
import pytest

GATEWAY_URL = "http://ai.local:11435"
# In a real test, we would get this from a proper auth flow, 
# but for smoke tests we assume localhost bypass or pre-known secret.
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET")

@pytest.mark.server_only
def test_coding_chat():
    print("Testing Coding LLM via Gateway Chat...")
    
    payload = {
        "model": "qwen3:8b", # Explicitly request a model that triggers coding signals
        "messages": [
            {"role": "user", "content": f"Update the file 'temp/test_coding.txt' with the content 'Hello from SharedLLM Agent at {time.time()}!' and reasoning 'Verification test.'"}
        ],
        "stream": False
    }
    
    headers = {
        "X-Internal-Secret": INTERNAL_SECRET,
        "X-User-ID": "jeremiah"
    }
    
    start_time = time.time()
    resp = httpx.post(f"{GATEWAY_URL}/api/chat", json=payload, headers=headers, timeout=60.0)
    duration = time.time() - start_time
    
    print(f"Response received in {duration:.2f}s")
    print(f"Status: {resp.status_code}")
    
    if resp.status_code == 200:
        data = resp.json()
        
        # Extract content (handle both OpenAI and Ollama formats)
        if "choices" in data:
            content = data["choices"][0].get("message", {}).get("content", "")
        else:
            content = data.get("message", {}).get("content", "")
            
        print(f"Content length: {len(content)}")
        print(f"Content preview: {content[:100]}...")
        
        # Check if orchestration happened
        if "Code Orchestration Success" in content or "Workflow Result" in content:
            print("SUCCESS: Coding orchestration detected in response.")
        else:
            print("FAILURE: Response did not indicate successful orchestration.")
            # It might be a regular chat response if the intent wasn't caught
            print(f"Intent was: {data.get('intent', 'unknown')}")
    else:
        print(f"FAILURE: {resp.text}")

if __name__ == "__main__":
    test_coding_chat()
