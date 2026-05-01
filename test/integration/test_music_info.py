
import requests
import os
from dotenv import load_dotenv

# Setup
script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)
load_dotenv(os.path.join(root_dir, '.env'))

API_URL = os.getenv("API_URL", "http://192.168.2.205:11435")
HEADERS = {"Content-Type": "application/json", "X-RAG-User": "admin"}

def log(msg): print(f"[MusicInfoTest] {msg}")

def test_music_info():
    log("Starting Music Info Test...")
    
    # List Playlists
    log("TEST 1: List Playlists")
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":"List my playlists"}]}, headers=HEADERS)
    log(f"Response: {r.text[:100]}...")
    assert r.status_code == 200

    # List Radio
    log("TEST 2: List Radio Stations")
    r = requests.post(f"{API_URL}/api/chat", json={"messages":[{"role":"user","content":"What radio stations do I have?"}]}, headers=HEADERS)
    log(f"Response: {r.text[:100]}...")
    assert r.status_code == 200

if __name__ == "__main__":
    test_music_info()
