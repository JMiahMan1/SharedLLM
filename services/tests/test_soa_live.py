"""
Live SOA Integration Testing Script.
Used for manual verification of Gateway, Fast Path, and Slow Path behavior in a live environment.
Related code: services/gateway/main.py
"""
import json
import os
import time

import requests

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:11435")

def _run_query(title, payload):
    print(f"\n=== {title} ===")
    try:
        start_time = time.time()
        resp = requests.post(f"{GATEWAY_URL}/api/chat", json=payload, timeout=30)
        duration = time.time() - start_time
        print(f"Status: {resp.status_code} ({duration:.2f}s)")
        print(json.dumps(resp.json(), indent=2))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    # 1. Fast Path - Light Control
    # 'Turn on' usually triggers the turn_on intent with high confidence.
    _run_query("HARDWARE: Piano-Lamp Control", {
        "query": "Turn on the Piano-Lamp",
        "voice_id": "default"
    })

    # 2. Fast Path - Media Play
    _run_query("FAST PATH: Media Play", {
        "query": "play some jazz in the kitchen",
        "voice_id": "default"
    })

    # 3. Slow Path - Conversational
    _run_query("SLOW PATH: Conversational", {
        "query": "How many states are in the USA?",
        "voice_id": "default"
    })

    # 4. Error Case - Unknown User
    _run_query("ERROR: Unknown User", {
        "query": "hello",
        "voice_id": "intruder_alert"
    })
