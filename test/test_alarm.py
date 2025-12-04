import os
import requests
import time
import json

API_URL = os.getenv("API_URL", "http://localhost:11435")

def run_test(name, query, expected_partial):
    print(f"\nTEST: {name}")
    print(f"Query: {query}")
    try:
        r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":query}]}, timeout=30)
        if r.status_code != 200:
            print(f"STATUS: FAIL (Status {r.status_code})")
            return False
            
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
    # 1. Time Query
    run_test("Time Check", "What time is it?", "it is currently")
    
    # 2. Set Alarm
    run_test("Set Alarm", "Set an alarm for 5 seconds", "alarm set")
    
    # 3. List Alarms
    run_test("List Alarms", "Show my alarms", "active alarms")
    
    # 4. Delete Alarm
    run_test("Delete Alarm", "Cancel all alarms", "cancelled")
