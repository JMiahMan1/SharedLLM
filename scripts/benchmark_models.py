import httpx
import time
import json
import os

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

PROMPT = """
Write a Python class 'MultiTenantLock' that uses Redis (redis-py) to implement a distributed lock.
The lock MUST be scoped to a 'user_id' and a 'resource_id'.
It should have 'acquire' and 'release' methods.
Ensure it handles timeouts and uses a TTL to prevent deadlocks.
Provide only the code.
"""

MODELS = [m.strip() for m in os.getenv("BENCHMARK_MODELS", "qwen3:8b,qwen2.5-coder:7b").split(",") if m.strip()]

async def run_benchmark():
    async with httpx.AsyncClient(timeout=120.0) as client:
        for model in MODELS:
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
                    print("--- Sample Output (First 200 chars) ---")
                    print(response_text[:200] + "...")
                else:
                    print(f"Error: Ollama returned {resp.status_code}")
            except Exception as e:
                print(f"Failed to benchmark {model}: {e}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_benchmark())
