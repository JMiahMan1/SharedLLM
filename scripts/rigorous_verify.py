import os
import time
import requests
from dotenv import load_dotenv

script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
load_dotenv(os.path.join(root_dir, '.env'))

API_URL = os.getenv("API_URL", "http://ai.local:11435")
HEADERS = {"Content-Type": "application/json", "X-RAG-User": "admin"}

def log(msg):
    print(msg, flush=True)
    with open("rigorous_verify_results.txt", "a") as f:
        f.write(msg + "\n")

def send_query(query, history=None):
    log(f"\n[USER] {query}")
    payload = {"messages": history if history else [{"role":"user","content":query}], "stream":False}
    if history:
        history.append({"role":"user", "content":query})
    
    try:
        r = requests.post(f"{API_URL}/api/chat", json=payload, headers=HEADERS, timeout=60)
        if r.status_code != 200:
            log(f"   [ERROR] HTTP {r.status_code}: {r.text}")
            return None, {}
        
        data = r.json()
        msg = data.get("message", {}).get("content", "")
        log(f"   [AI] {msg}")
        return msg, data
    except Exception as e:
        log(f"   [EXCEPTION] {e}")
        return None, {}

def test_media_lifecycle():
    log("\n=== TEST: Media Lifecycle (TV + Music) ===")
    send_query("Turn on the Office TV")
    time.sleep(2)
    send_query("Play Brandon Lake on the Office TV")
    time.sleep(5)
    send_query("Skip this song")
    time.sleep(2)
    send_query("Stop the music on Office TV")
    time.sleep(2)
    send_query("Turn off the Office TV")
    time.sleep(2)

def test_compound_patterns():
    log("\n=== TEST: Compound Patterns & Color ===")
    send_query("Turn on the Kitchen lights and Bathroom lights")
    time.sleep(2)
    send_query("Turn off all lights in the Kitchen and Bathroom")
    time.sleep(2)
    send_query("Turn on lights in Kitchen and Bathroom")
    time.sleep(2)
    send_query("Turn Kitchen Light 1 Blue") # Assuming valid entity
    time.sleep(2)

def test_crud_tools():
    log("\n=== TEST: CRUD Tools ===")
    ts = int(time.time())
    send_query(f"Add a calendar event Test_Event_{ts} tomorrow at 9am")
    send_query("List my appointments")
    send_query(f"Cancel the meeting Test_Event_{ts}")
    # Notes
    send_query(f"Create a note called Test_Note_{ts} saying 'Verification'")
    send_query(f"Read my Test_Note_{ts} note")
    send_query(f"Delete note Test_Note_{ts}")

def test_context_and_search():
    log("\n=== TEST: Context, History & Search ===")
    
    # 1. Chat History
    log(">> Step 1: Contextual Query (President)")
    # We must manually maintain history for this stateless API test unless the server tracks it by user.
    # The server *does* have a 'state' via history manager but for the test script let's simulate a session if needed.
    # Actually, the API is stateful per user (X-RAG-User), so consecutive calls should work.
    
    resp1, _ = send_query("Who is the president of France?")
    if resp1 and "Macron" in resp1:
        log("   [PASS] Identified President.")
    
    resp2, _ = send_query("Who is his wife?")
    if resp2 and "Brigitte" in resp2:
        log("   [PASS] Context maintained (Brigitte identified).")
    else:
        log("   [FAIL] Context lost.")

    # 2. Web Search
    log(">> Step 2: Web Search")
    resp3, _ = send_query("Search for the current stock price of Apple")
    if resp3 and ("$" in resp3 or "USD" in resp3):
         log("   [PASS] Search returned financial data.")
    else:
         log("   [WARN] Search might have failed or been generic.")

    # 3. RAG (Knowledge)
    log(">> Step 3: RAG Retrieval")
    resp4, _ = send_query("What devices are in the Office?") 
    if resp4 and "Office" in resp4:
        log("   [PASS] RAG context usage plausible.")

if __name__ == "__main__":
    with open("rigorous_verify_results.txt", "w") as f:
        f.write(f"Rigorous Verification Run 2: {time.ctime()}\n")
    
    test_media_lifecycle()
    test_compound_patterns()
    test_crud_tools() # Timer implicitly tested via manual check request, skipping here to save time? 
                      # User said "also with the set timer...". I'll add it back briefly.
    send_query("Set a timer for 10 seconds on the Office TV")
    time.sleep(11) # Wait for it
    test_context_and_search()
