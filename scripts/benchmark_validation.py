import ast
import asyncio
import contextlib
import json
import os
import time

import httpx

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

PROMPT = """
Write a Python class 'MultiTenantLock' that uses Redis to implement a distributed lock.
The lock MUST be scoped to a 'user_id' and a 'resource_id'.
It MUST have 'acquire' and 'release' methods.
Provide ONLY the code, no explanation.
"""

MODELS = ["qwen2.5-coder:7b", "qwen3.5:9b"]

async def unload_model(model):
    async with httpx.AsyncClient(timeout=30.0) as client:
        print(f"Unloading model: {model}...")
        with contextlib.suppress(Exception):
            # Sending keep_alive: 0 to force immediate eviction
            await client.post(f"{OLLAMA_URL}/api/generate", json={"model": model, "keep_alive": 0})

def validate_code(code: str):
    try:
        # Extract first python block if present
        if "```python" in code:
            code = code.split("```python")[1].split("```")[0].strip()
        elif "```" in code:
            code = code.split("```")[1].split("```")[0].strip()

        tree = ast.parse(code)

        has_class = False
        has_acquire = False
        has_release = False

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "MultiTenantLock":
                has_class = True
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        if item.name == "acquire":
                            has_acquire = True
                        if item.name == "release":
                            has_release = True

        return {
            "valid_syntax": True,
            "has_class": has_class,
            "has_acquire": has_acquire,
            "has_release": has_release,
            "is_complete": has_class and has_acquire and has_release
        }
    except Exception as e:
        return {"valid_syntax": False, "error": str(e)}

async def run_validation_benchmark():
    async with httpx.AsyncClient(timeout=600.0) as client:
        results = {}
        for model in MODELS:
            # Ensure clean state
            await unload_model(model)
            await asyncio.sleep(5)

            print(f"\n--- Testing Model: {model} (Clean State) ---")
            start_t = time.time()
            try:
                resp = await client.post(
                    f"{OLLAMA_URL}/api/generate",
                    json={"model": model, "prompt": PROMPT, "stream": False},
                )
                elapsed = time.time() - start_t
                if resp.status_code == 200:
                    code = resp.json().get("response", "")
                    metrics = validate_code(code)
                    results[model] = {
                        "elapsed": elapsed,
                        "metrics": metrics
                    }
                    print(f"Elapsed: {elapsed:.2f}s")
                    print(f"Validation: {metrics}")
                else:
                    print(f"Error: {resp.status_code}")
            except Exception as e:
                print(f"Failed {model}: {e}")

            # Unload again to be clean for next
            await unload_model(model)
            await asyncio.sleep(5)

        print("\n=== FINAL RESULTS ===")
        print(json.dumps(results, indent=2))

if __name__ == "__main__":
    asyncio.run(run_validation_benchmark())
