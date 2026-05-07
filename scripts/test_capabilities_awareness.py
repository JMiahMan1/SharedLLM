import httpx
import json
import sys

GATEWAY_URL = "http://192.168.2.205:8080/api/chat"
INTERNAL_SECRET = "change-me-in-production"

tests = [
    "Send a voice message to Alice saying 'I have been upgraded with new capabilities!'",
    "Show me the last 50 lines of logs for the sharedllm_gateway container",
    "Trigger a re-index of your own tool capabilities",
    "What is the current status of my storage indexing?",
    "Add a new calendar event for dinner at 7pm tonight"
]

def run_tests():
    print(f"Testing Gateway at {GATEWAY_URL}...")
    for query in tests:
        print(f"\n[QUERY]: {query}")
        try:
            resp = httpx.post(
                GATEWAY_URL,
                json={
                    "query": query,
                    "stream": False,
                    "rag_user": "jeremiah"
                },
                headers={
                    "Authorization": f"Bearer {INTERNAL_SECRET}",
                    "X-Internal-Secret": INTERNAL_SECRET
                },
                timeout=60.0
            )
            if resp.status_code == 200:
                data = resp.json()
                print(f"[STATUS]: {data.get('status')}")
                print(f"[INTENT]: {data.get('intent')}")
                print(f"[EXECUTION]: {json.dumps(data.get('execution_result'), indent=2)}")
                print(f"[MESSAGE]: {data.get('message')}")
            else:
                print(f"[ERROR]: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"[EXCEPTION]: {e}")

if __name__ == "__main__":
    run_tests()
