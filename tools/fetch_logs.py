import requests
import json
import sys

URL = "http://192.168.2.211:11435/api/admin/logs?lines=1000"

try:
    r = requests.get(URL, timeout=10)
    data = r.json()
    logs = data.get("logs", [])
    
    print(f"Fetched {len(logs)} log lines.")
    
    found_error = False
    for i, line in enumerate(logs):
        l_lower = line.lower()
        if "error" in l_lower or "exception" in l_lower or "traceback" in l_lower or "500 internal" in l_lower:
            # Print context
            print("--- ERROR CONTEXT ---")
            for j in range(max(0, i-5), min(len(logs), i+20)):
                print(logs[j].strip())
            print("---------------------")
            found_error = True
            
    if not found_error:
        print("No explicit ERROR/Exception found in last 1000 lines.")
        
except Exception as e:
    print(f"Failed to fetch logs: {e}")
