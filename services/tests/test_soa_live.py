import requests
import json
import time

GATEWAY_URL = "http://localhost:11435"

def test_query(title, payload):
    print(f"\n=== {title} ===")
    try:
        start_time = time.time()
        resp = requests.post(f"{GATEWAY_URL}/api/chat", json=payload, timeout=15)
        duration = time.time() - start_time
        print(f"Status: {resp.status_code} ({duration:.2f}s)")
        print(json.dumps(resp.json(), indent=2))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    # 1. Fast Path - Light Control
    # 'Turn on' usually triggers the turn_on intent with high confidence.
    test_query("FAST PATH: Light Control", {
        "query": "Turn on the living room lights",
        "voice_id": "default"
    })

    # 2. Fast Path - Media Play
    test_query("FAST PATH: Media Play", {
        "query": "play some jazz in the kitchen",
        "voice_id": "default"
    })

    # 3. Slow Path - Conversational
    test_query("SLOW PATH: Conversational", {
        "query": "How many states are in the USA?",
        "voice_id": "default"
    })

    # 4. Error Case - Unknown User
    test_query("ERROR: Unknown User", {
        "query": "hello",
        "voice_id": "intruder_alert"
    })
