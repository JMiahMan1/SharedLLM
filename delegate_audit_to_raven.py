import httpx
import asyncio
import json

SERVER_IP = "192.168.2.205"
GATEWAY_URL = f"http://{SERVER_IP}:8080"
API_KEY = "c7133d7546cee7bbd04dcf48cb7efc2bf3a080d7a1032ac3"

MISSION = """
RAVEN, I need you to perform a full system audit on the SharedLLM production environment.

1. **Log Audit**: Use `DockerLogsRequest` to check for recent errors in all microservices:
   - gateway
   - identity
   - execution
   - rag
   - storage
   - logging
   - workspace_runtime

2. **Test Execution**: Run ALL available integration and unit tests using `WorkspaceShellRequest`. 
   The following tests MUST be run:
   - python3 test_frontend_api.py
   - python3 test_llm.py
   - python3 test_gateway.py
   - python3 test_remote.py
   - python3 test_stream.py
   - pytest tests/integration/test_workspace_lifecycle.py

3. **Auto-Repair**: If you find any breakages, regressions, or service failures, fix them immediately using your workspace file tools (WorkspaceFileReadRequest, WorkspaceFilePatchRequest, WorkspaceFileWriteRequest).

4. **Validation**: Verify your fixes by re-running the failing tests.

5. **Reporting**: Provide a detailed summary of your findings, the actions you took, and the final status of all tests.

This is a CRITICAL mission. Proceed immediately.
"""

async def delegate_to_raven():
    print("Delegating system audit to Raven...")
    
    payload = {
        "query": MISSION,
        "stream": True, # We want to see Raven's progress
        "rag_user": "default"
    }
    
    headers = {
        "Authorization": f"Bearer {API_KEY}"
    }
    
    async with httpx.AsyncClient(timeout=600.0) as client:
        try:
            async with client.stream("POST", f"{GATEWAY_URL}/api/chat", json=payload, headers=headers) as resp:
                print(f"Status: {resp.status_code}")
                if resp.status_code != 200:
                    print(f"Error: {await resp.aread()}")
                    return
                
                async for line in resp.aiter_lines():
                    if not line: continue
                    if line.startswith("data: "):
                        try:
                            chunk = json.loads(line[6:])
                            content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if content:
                                print(content, end="", flush=True)
                        except:
                            pass
        except Exception as e:
            print(f"\nDelegation failed: {e}")

if __name__ == "__main__":
    asyncio.run(delegate_to_raven())
