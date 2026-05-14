import httpx
import os
import json
import time

# Load secrets from environment (populated by deploy.sh or manual export)
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET")
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8080")

MODELS_TO_TEST = [] # Will be populated via /api/models

AUDIT_LOG_PATH = "/home/jeremiah/.gemini/antigravity/brain/1403f7bd-1016-4690-8270-4b98d9503c62/artifacts/raven_pipeline_audit.md"

def update_audit_inventory(models):
    try:
        with open(AUDIT_LOG_PATH, "r") as f:
            lines = f.readlines()
        
        with open(AUDIT_LOG_PATH, "w") as f:
            for line in lines:
                if "Active Models**: [Awaiting" in line:
                    f.write(f"*   **Active Models**: {', '.join(models)}\n")
                else:
                    f.write(line)
        print(f"Updated Audit Log inventory with {len(models)} models.")
    except Exception as e:
        print(f"Could not update Audit Log: {e}")

TASKS = [
    {"name": "fast_path", "query": "turn on the office lights", "expected_type": "intent"},
    {"name": "tool_use", "query": "list my files on nextcloud", "expected_type": "tool_call"},
    {"name": "code_gen", "query": "write a python function to calculate fibonacci", "expected_type": "text"}
]

def log_result(model_name, task_name, success, latency):
    print(f"[{model_name}] Task: {task_name} | Success: {success} | Latency: {latency:.2f}s")

async def run_benchmark():
    if not INTERNAL_SECRET:
        print("ERROR: INTERNAL_SECRET is missing. Run with export INTERNAL_SECRET=...")
        return

    async with httpx.AsyncClient(timeout=120.0) as client:
        # 1. Fetch actual models
        try:
            m_resp = await client.get(f"{GATEWAY_URL}/api/models")
            if m_resp.status_code == 200:
                discovered = m_resp.json().get("models", [])
                update_audit_inventory(discovered)
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
                MODELS_TO_TEST.append("qwen2.5-coder:7b") # fallback
        except Exception as e:
            print(f"Discovery error: {e}")
            MODELS_TO_TEST.append("qwen2.5-coder:7b")

        for model_id in MODELS_TO_TEST:
            print(f"\n--- Benchmarking Model: {model_id} ---")
            for task in TASKS:
                start_time = time.time()
                try:
                    resp = await client.post(
                        f"{GATEWAY_URL}/api/chat/completions",
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
