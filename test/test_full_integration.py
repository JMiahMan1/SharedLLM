import os
import requests
import time

API_URL = os.getenv("API_URL", "http://localhost:11435")

def run_test(name, query, expected_partial):
    print(f"\nTEST: {name}")
    print(f"Query: {query}")
    try:
        r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":query}]}, timeout=30)
        ans = r.json().get("message", {}).get("content", "")
        print(f"Result: {ans[:100]}...")
        if expected_partial.lower() in ans.lower():
            print("STATUS: PASS")
            return True
        else:
            print(f"STATUS: FAIL (Expected '{expected_partial}')")
            return False
    except Exception as e:
        print(f"STATUS: ERROR ({e})")
        return False

if __name__ == "__main__":
    # 1. Basic RAG
    run_test("Search", "Who is the president of France?", "macron")
    
    # 2. Calendar
    run_test("Calendar List", "List my calendars", "Available Calendars")
    
    # 3. Media
    run_test("Media Power", "Turn on Office TV", "Sent command")
    
    # 4. Multi-Command
    run_test("Multi-Cmd", "Turn off Office TV and turn on Piano Lamp", "Sent command")
    
    # 5. Context
    run_test("Context", "What is his wife's name?", "brigitte")
