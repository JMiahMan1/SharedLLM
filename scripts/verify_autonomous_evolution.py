import httpx
import json
import time

GATEWAY_URL = "http://192.168.2.205:8080/api/chat"
RAG_URL = "http://192.168.2.205:8080/api/storage" # Or internal if possible, but let's hit gateway
INTERNAL_SECRET = "change-me-in-production"

tests = [
    {
        "name": "Self-Indexing Awareness",
        "query": "Trigger a re-index of your own tool capabilities",
        "expected_action": "CapabilityIndexRequest",
        "validate": lambda d: d.get("execution_result", {}).get("status") == "SUCCESS"
    },
    {
        "name": "Docker Log Telemetry",
        "query": "Show me the last 20 lines of logs for the sharedllm_gateway container",
        "expected_action": "DockerLogsRequest",
        "validate": lambda d: d.get("execution_result", {}).get("status") == "SUCCESS" and len(d.get("execution_result", {}).get("detail", {}).get("lines", [])) > 0
    },
    {
        "name": "Messaging System",
        "query": "List my Nextcloud Talk conversations",
        "expected_action": "TalkRequest",
        "validate": lambda d: d.get("execution_result", {}).get("status") == "SUCCESS"
    },
    {
        "name": "Workspace Awareness",
        "query": "List the files in my current workspace",
        "expected_action": "FileListRequest",
        "validate": lambda d: d.get("execution_result", {}).get("status") == "SUCCESS"
    }
]

def run_verification():
    print("Starting Autonomous Evolution Verification Suite...")
    report = "# Autonomous Evolution Verification Report\n\n"
    
    # 1. Clear and Re-index (Optional but recommended for clean test)
    # print("Purging old capabilities...")
    # ... (skipping purge for speed unless needed)

    for test in tests:
        name = test["name"]
        query = test["query"]
        print(f"\n[TEST]: {name}")
        print(f"  Query: {query}")
        
        try:
            resp = httpx.post(
                GATEWAY_URL,
                json={"query": query, "stream": False, "rag_user": "jeremiah"},
                headers={"X-Internal-Secret": INTERNAL_SECRET, "Authorization": f"Bearer {INTERNAL_SECRET}"},
                timeout=120.0
            )
            
            if resp.status_code == 200:
                data = resp.json()
                passed = test["validate"](data)
                status_str = "PASS" if passed else "FAIL"
                print(f"  Status: {status_str}")
                print(f"  Detected Action: {data.get('intent')}")
                
                report += f"## {name}: {status_str}\n"
                report += f"- **Query**: {query}\n"
                report += f"- **Intent**: {data.get('intent')}\n"
                report += f"- **LLM Output**:\n```\n{data.get('message', {}).get('content', '')}\n```\n"
                if data.get('execution_result'):
                    report += f"- **Execution Status**: {data.get('execution_result').get('status')}\n"
                    report += f"- **Execution Detail**:\n```json\n{json.dumps(data.get('execution_result').get('detail'), indent=2)}\n```\n"
                report += "\n---\n"
            else:
                print(f"  ERROR: {resp.status_code}")
                report += f"## {name}: ERROR ({resp.status_code})\n\n"
        except Exception as e:
            print(f"  EXCEPTION: {e}")
            report += f"## {name}: EXCEPTION ({e})\n\n"

    with open("docs/autonomous_verification_report.md", "w") as f:
        f.write(report)
    print("\nVerification complete. Report generated at docs/autonomous_verification_report.md")

if __name__ == "__main__":
    run_verification()
