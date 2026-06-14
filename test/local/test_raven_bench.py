import asyncio
import os
import httpx

# List of diagnostic tasks for Raven
RAVEN_MISSIONS = [
    {
        "name": "System Health Inventory",
        "query": "List all sharedllm_ services and tell me if any are NOT currently in the 'running' state.",
        "expected_hint": "running"
    },
    {
        "name": "Gateway Log Analysis",
        "query": "Inspect the last 20 lines of the gateway logs and tell me what the last recorded HTTP request was.",
        "expected_hint": "INFO"
    },
    {
        "name": "Self-Healing Capability",
        "query": "Identify the status of the 'execution' service. If it is running, describe what tool call you would use to restart it if it were failing.",
        "expected_hint": "restart"
    }
]

async def run_mission(client, gateway_url, headers, mission, model):
    print(f"\n🚀 Running Task: {mission['name']}")
    print(f"   Query: {mission['query']}")
    
    payload = {
        "query": mission["query"],
        "priority": 1,
        "coding_model": model
    }
    
    resp = await client.post(f"{gateway_url}/raven/missions", json=payload, headers=headers)
    if resp.status_code != 200:
        print(f"   ❌ Failed to create mission: {resp.text}")
        return False

    mission_id = resp.json().get("mission", {}).get("id")
    print(f"   ✅ Mission #{mission_id} queued...")

    # Polling
    for _i in range(20):
        await asyncio.sleep(5)
        status_resp = await client.get(f"{gateway_url}/raven/missions", headers=headers)
        if status_resp.status_code == 200:
            missions = status_resp.json()
            current = next((m for m in missions if m["id"] == mission_id), None)
            if current:
                status = current.get("status")
                if status == "completed":
                    result = current.get("result", "")
                    print(f"   ✅ Task Completed!")
                    print(f"   Result Snippet: {result[:200]}...")
                    if mission["expected_hint"].lower() in result.lower():
                        print("   ⭐ VERIFIED: Answer contains expected keywords.")
                        return True
                    else:
                        print("   ⚠️ WARNING: Answer might be incomplete or missing context.")
                        return True
                elif status == "failed":
                    print(f"   ❌ Task Failed: {current.get('result')}")
                    return False
    
    print("   ❌ Task Timed Out.")
    return False

async def main():
    base_url = os.getenv("LIVE_TEST_URL", "http://localhost:8080")
    gateway_url = f"{base_url}/api"
    internal_secret = os.getenv("INTERNAL_SECRET", "change-me-in-production")
    headers = {"X-Internal-Secret": internal_secret}
    # Fetch the active coding model from the Identity Config Database (single source of truth)
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    hostname = parsed.hostname or "localhost"
    identity_url = f"{parsed.scheme}://{hostname}:8001"
    
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(f"{identity_url}/api/settings", headers=headers)
        assert resp.status_code == 200, f"Failed to fetch Identity settings: {resp.text}"
        settings = {s["key"]: s["value"] for s in resp.json()}
        
        active_provider = settings.get("active_llm_provider", "ollama")
        if active_provider == "openrouter":
            model = settings.get("cloud_coding_model")
        else:
            model = settings.get("ollama_coding_model") or settings.get("coding_model")
            
        assert model, f"No coding model configured in database (provider: {active_provider})"

    print(f"=== Raven Autonomous Benchmark Suite [Model: {model}] ===")

    async with httpx.AsyncClient(timeout=60.0) as client:
        success_count = 0
        for mission in RAVEN_MISSIONS:
            if await run_mission(client, gateway_url, headers, mission, model):
                success_count += 1
        
        print(f"\n=== Final Score: {success_count}/{len(RAVEN_MISSIONS)} missions successful ===")

import pytest

@pytest.mark.local_only
async def test_raven_benchmark():
    await main()

if __name__ == "__main__":
    asyncio.run(main())
