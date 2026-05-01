
import requests
import os
from dotenv import load_dotenv

# Setup
script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
load_dotenv(os.path.join(root_dir, '.env'))

API_URL = os.getenv("API_URL", "http://192.168.2.205:11435")
HEADERS = {"Content-Type": "application/json", "X-RAG-User": "admin"}

def log(msg): print(f"[SearchTest] {msg}")

def test_web_search():
    log("Starting Web Search Test...")
    
    query = "Search the web for the current time in Tokyo"
    log(f"Query: {query}")
    
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":query}]}, headers=HEADERS)
    content = r.json().get("message", {}).get("content", "")
    log(f"Response: {content[:150]}...")
    
    if "Tokyo" in content or "PM" in content or "AM" in content or ":" in content:
        log("✅ Search returned relevant result.")
    else:
        log("❌ Search response seems irrelevant or empty.")

if __name__ == "__main__":
    test_web_search()
