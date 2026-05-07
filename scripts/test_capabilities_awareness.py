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
    report = "# Capability Awareness Test Report\n\n"
    for query in tests:
        print(f"\n[QUERY]: {query}")
        report += f"## Query: {query}\n"
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
                timeout=120.0
            )
            if resp.status_code == 200:
                data = resp.json()
                print(f"[STATUS]: {data.get('status')}")
                report += f"- **Status**: {data.get('status')}\n"
                report += f"- **Intent**: {data.get('intent')}\n"
                report += f"- **LLM Response**:\n\n```\n{data.get('message', {}).get('content', '')}\n```\n\n"
                if data.get('execution_result'):
                    report += f"- **Execution Result**:\n\n```json\n{json.dumps(data.get('execution_result'), indent=2)}\n```\n\n"
            else:
                print(f"[ERROR]: {resp.status_code}")
                report += f"- **ERROR**: {resp.status_code}\n\n"
        except Exception as e:
            print(f"[EXCEPTION]: {e}")
            report += f"- **EXCEPTION**: {e}\n\n"
    
    with open("docs/capability_test_report.md", "w") as f:
        f.write(report)
    print("\nReport generated at docs/capability_test_report.md")

if __name__ == "__main__":
    run_tests()
