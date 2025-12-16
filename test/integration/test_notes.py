import requests
import os
import time
from dotenv import load_dotenv

# Setup
script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
load_dotenv(os.path.join(root_dir, '.env'))

API_URL = os.getenv("API_URL", "http://192.168.2.211:11435")
HEADERS = {"Content-Type": "application/json", "X-RAG-User": "admin"}

def test_note_lifecycle():
    print("--- Testing Note Lifecycle ---")
    
    timestamp = int(time.time())
    note_title = f"TestNote_{timestamp}"
    content_body = "This is a test note content."
    
    # 1. CREATE
    print(f"[1] Creating Note '{note_title}'...")
    create_cmd = f"Create a note called {note_title} that says {content_body}"
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":create_cmd}]}, headers=HEADERS)
    print(f"Response: {r.text[:200]}...")
    assert "success" in r.text.lower() or "created" in r.text.lower() or "saved" in r.text.lower()

    # 2. READ
    print(f"[2] Reading Note '{note_title}'...")
    read_cmd = f"Read the note {note_title}"
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":read_cmd}]}, headers=HEADERS)
    print(f"Response: {r.text[:200]}...")
    assert content_body in r.text

    # 3. APPEND
    print(f"[3] Appending to Note '{note_title}'...")
    append_cmd = f"Add 'Buy Milk' to my {note_title} note"
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":append_cmd}]}, headers=HEADERS)
    print(f"Response: {r.text[:200]}...")
    
    # 4. READ AGAIN
    print(f"[4] Verifying Append...")
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":read_cmd}]}, headers=HEADERS)
    print(f"Response: {r.text[:200]}...")
    assert "Buy Milk" in r.text

    print("✅ Note Lifecycle Passed")

if __name__ == "__main__":
    test_note_lifecycle()
