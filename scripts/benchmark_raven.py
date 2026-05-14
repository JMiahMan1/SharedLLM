import httpx
import os
import json
import time

# Load secrets from environment
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET")
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8080")

MODELS_TO_TEST = []
RESULTS_FILE = "data/raven_audit_results.json"

def log_result(model_name, task_name, success, latency):
    print(f"[{model_name}] Task: {task_name} | Success: {success} | Latency: {latency:.2f}s")
    # Save raw results for retrieval
    results = []
    if os.path.exists(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r") as f:
                results = json.load(f)
        except: pass
    
    results.append({
        "model": model_name,
        "task": task_name,
        "success": success,
        "latency": latency,
        "timestamp": time.time()
    })
    
    os.makedirs("data", exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2)

async def run_benchmark():
    if not INTERNAL_SECRET:
        print("ERROR: INTERNAL_SECRET is missing.")
        return

    async with httpx.AsyncClient(timeout=120.0) as client:
        # 1. Fetch actual models
        try:
            m_resp = await client.get(f"{GATEWAY_URL}/api/models")
            if m_resp.status_code == 200:
                discovered = m_resp.json().get("models", [])
                print(f"Discovered models: {discovered}")
                
                # Sort models: small first, large last
                small_models = []
                large_models = []
                for m in discovered:
                    m_lower = m.lower()
                    if "35b" in m_lower or "70b" in m_lower:
                        large_models.append(m)
                    elif any(x in m_lower for x in ["7b", "8b", "9b"]):
                        small_models.append(m)
                
                MODELS_TO_TEST.extend(small_models)
                MODELS_TO_TEST.extend(large_models)
            else:
                print(f"Warning: Could not fetch models ({m_resp.status_code})")
                MODELS_TO_TEST.append("qwen2.5-coder:7b")
        except Exception as e:
            print(f"Discovery error: {e}")
            MODELS_TO_TEST.append("qwen2.5-coder:7b")

        TASKS = [
            {"name": "fast_path", "query": "turn on the office lights"},
            {"name": "tool_use", "query": "list my files on nextcloud"},
            {"name": "code_gen", "query": "write a python function to calculate fibonacci"}
        ]

        for model_id in MODELS_TO_TEST:
            print(f"\n--- Benchmarking Model: {model_id} ---")
            for task in TASKS:
                start_time = time.time()
                try:
                    resp = await client.post(
                        f"{GATEWAY_URL}/v1/chat/completions",
                        json={
                            "model": model_id,
                            "messages": [{"role": "user", "content": task["query"]}],
                            "stream": False
                        },
                        headers={"X-Internal-Secret": INTERNAL_SECRET}
                    )
                    latency = time.time() - start_time
                    
                    if resp.status_code == 200:
                        log_result(model_id, task["name"], True, latency)
                    else:
                        log_result(model_id, task["name"], False, latency)
                        print(f"  Error: {resp.text}")
                except Exception as e:
                    latency = time.time() - start_time
                    log_result(model_id, task["name"], False, latency)
                    print(f"  Exception: {str(e)}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(run_benchmark())
