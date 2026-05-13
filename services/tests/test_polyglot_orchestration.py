import httpx
import json
import time

GATEWAY_URL = "http://ai.local:11435"
INTERNAL_SECRET = "change-me-in-production"

def run_coding_task(description, expected_markers, workspace_id="SharedLLM", relative_path=None):
    print(f"\n>>> Task: {description}")
    payload = {
        "model": "qwen3:8b",
        "messages": [{"role": "user", "content": description}],
        "stream": False
    }
    headers = {"X-Internal-Secret": INTERNAL_SECRET, "X-User-ID": "jeremiah"}
    
    start_time = time.time()
    resp = httpx.post(f"{GATEWAY_URL}/api/chat", json=payload, headers=headers, timeout=120.0)
    duration = time.time() - start_time
    
    print(f"Status: {resp.status_code} ({duration:.2f}s)")
    if resp.status_code == 200:
        data = resp.json()
        content = data.get("message", {}).get("content", "") if "message" in data else data.get("choices", [{}])[0].get("message", {}).get("content", "")
        
        # Verify LLM response quality
        print(f"Response (truncated): {content[:150]}...")
        missing = [m for m in expected_markers if m not in content]
        if missing:
            print(f"RESULT: FAILURE (Missing markers: {missing})")
            print("--- FULL RESPONSE ---")
            print(content)
            print("---------------------")
            return False

        # ACCURACY CHECK: Verify the file actually exists and has content
        if relative_path:
            verify_payload = {"workspace_id": workspace_id, "relative_path": relative_path}
            # Note: We call the runtime directly to verify the source of truth
            RUNTIME_URL = "http://ai.local:8007" # SharedLLM's workspace_runtime
            v_resp = httpx.post(f"{RUNTIME_URL}/files/read", json=verify_payload, headers=headers)
            if v_resp.status_code == 200:
                file_data = v_resp.json()
                actual_content = file_data.get("content", "")
                file_size = len(actual_content)
                print(f"VERIFICATION: File '{relative_path}' exists ({file_size} bytes)")
                
                if file_size > 10:
                    print(f"VERIFICATION SUCCESS: File verified on disk. Size: {file_size} bytes.")
                else:
                    print(f"VERIFICATION FAILURE: File on disk is too small ({file_size} bytes).")
                    return False
            else:
                print(f"VERIFICATION: ERROR - Could not read file {relative_path}: {v_resp.text}")
                return False

        print("RESULT: SUCCESS")
        return True
    else:
        print(f"RESULT: ERROR - {resp.text}")
        return False

def test_polyglot():
    ts = int(time.time())
    tasks = [
        {
            "desc": f"Create a bash script 'polyglot/hello_{ts}.sh' that prints 'Bash is alive'. Lint it with 'shellcheck' and run it to verify functionality.",
            "markers": ["Code Orchestration Success", "Bash is alive", "Developer Reasoning & Description"],
            "path": f"polyglot/hello_{ts}.sh"
        },
        {
            "desc": f"Create a Python program 'polyglot/hello_{ts}.py' that calculates 2+2 and prints the result. Lint it with 'flake8' and run it.",
            "markers": ["Code Orchestration Success", "4", "Developer Reasoning & Description"],
            "path": f"polyglot/hello_{ts}.py"
        },
        {
            "desc": f"Create a simple Go program 'polyglot/hello_{ts}.go' that prints 'Go is fast'. Verify it with 'go run' and 'go vet'.",
            "markers": ["Code Orchestration Success", "Go is fast", "Developer Reasoning & Description"],
            "path": f"polyglot/hello_{ts}.go"
        },
        {
            "desc": f"Create a Node.js script 'polyglot/hello_{ts}.js' that prints 'Node is here'. Verify it with 'node' functionality test.",
            "markers": ["Code Orchestration Success", "Node is here", "Developer Reasoning & Description"],
            "path": f"polyglot/hello_{ts}.js"
        }
    ]
    
    overall_success = True
    for t in tasks:
        if not run_coding_task(t["desc"], t["markers"], relative_path=t["path"]):
            overall_success = False
            
    if overall_success:
        print("\nALL POLYGLOT TESTS PASSED!")
    else:
        print("\nSOME POLYGLOT TESTS FAILED.")
        exit(1)

if __name__ == "__main__":
    test_polyglot()
