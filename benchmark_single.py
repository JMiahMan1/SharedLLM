import httpx
import time
import json
import os

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://192.168.2.114:11434")

PROMPT = """
Write a Python class 'MultiTenantLock' that uses Redis (redis-py) to implement a distributed lock.
The lock MUST be scoped to a 'user_id' and a 'resource_id'.
It should have 'acquire' and 'release' methods.
Ensure it handles timeouts and uses a TTL to prevent deadlocks.
Provide only the code.
"""

async def run_single_benchmark(model):
    async with httpx.AsyncClient(timeout=300.0) as client:
        print(f"\n--- Benchmarking Model: {model} ---")
        start_t = time.time()
        try:
            resp = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": model, "prompt": PROMPT, "stream": False},
            )
            elapsed = time.time() - start_t
            if resp.status_code == 200:
                data = resp.json()
                response_text = data.get("response", "")
                print(f"Elapsed: {elapsed:.2f}s")
                print(f"Response Length: {len(response_text)} chars")
                print("--- Sample Output ---")
                print(response_text)
            else:
                print(f"Error: Ollama returned {resp.status_code}")
        except Exception as e:
            print(f"Failed to benchmark {model}: {e}")

if __name__ == "__main__":
    import asyncio
    import sys
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen3.5:9b"
    asyncio.run(run_single_benchmark(model))
