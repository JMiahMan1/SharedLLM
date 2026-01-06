
import requests
import json
import time

API_URL = "http://192.168.2.211:11435/api/chat"
headers = {"Content-Type": "application/json"}

query = "Play Brandon Lake on Gracies TV"
payload = {"query": query}

print(f"Sending Query: '{query}'")
try:
    start = time.time()
    response = requests.post(API_URL, json=payload, headers=headers, timeout=60)
    duration = time.time() - start
    
    print(f"Status Code: {response.status_code}")
    print(f"Duration: {duration:.2f}s")
    
    try:
        data = response.json()
        print(json.dumps(data, indent=2))
        
        # Extract response message
        print("\nResponse Message:")
        print(data.get("response", "No response text found"))
        
    except json.JSONDecodeError:
        print("Response not JSON:")
        print(response.text)

except Exception as e:
    print(f"Request Failed: {e}")
