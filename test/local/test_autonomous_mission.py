import asyncio
import os
import httpx
import pytest

@pytest.mark.local_only
async def test_autonomous_mission():
    """
    Tests the Raven Autonomous Mission pipeline.
    """
    base_url = os.getenv("LIVE_TEST_URL", "http://localhost:8080")
    gateway_url = f"{base_url}/api"
    internal_secret = os.getenv("INTERNAL_SECRET", "change-me-in-production")
    headers = {"X-Internal-Secret": internal_secret}
    
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(f"{gateway_url}/config")
        assert resp.status_code == 200, f"Failed to fetch Gateway config: {resp.text}"
        config = resp.json().get("config", {})
        model_name = config.get("coding_model")
        assert model_name, "No coding model configured in Gateway config"

    print(f"=== Testing Raven Autonomous Mission against {base_url} [Model: {model_name}] ===\n")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. Create a Mission
        print("1. Creating Autonomous Mission...")
        mission_request = {
            "query": "System diagnostic: Check the health of the control_plane and summarize any recent startup logs.",
            "priority": 1,
            "coding_model": model_name
        }
        
        # We need a user session/creds usually, but for this test we'll assume the internal secret bypass works
        # or we use the admin mission endpoint if available
        resp = await client.post(f"{gateway_url}/raven/missions", json=mission_request, headers=headers)
        
        if resp.status_code != 200:
            print(f"❌ Failed to create mission: {resp.status_code} - {resp.text}")
            return

        mission = resp.json().get("mission", {})
        mission_id = mission.get("id")
        print(f"✅ Mission #{mission_id} created. Status: {mission.get('status')}")

        # 2. Poll for completion
        print("\n2. Monitoring Mission Execution...")
        max_retries = 30
        for i in range(max_retries):
            status_resp = await client.get(f"{gateway_url}/raven/missions", headers=headers)
            if status_resp.status_code == 200:
                missions = status_resp.json()
                current = next((m for m in missions if m["id"] == mission_id), None)
                
                if current:
                    status = current.get("status")
                    print(f"   [Step {i+1}] Status: {status}")
                    
                    if status == "completed":
                        print("\n✅ MISSION COMPLETED!")
                        print("-" * 40)
                        print("Result:", current.get("result"))
                        print("-" * 40)
                        return
                    elif status == "failed":
                        print(f"\n❌ MISSION FAILED: {current.get('result')}")
                        return
            
            await asyncio.sleep(5)
        
        print("\n❌ Mission timed out.")

if __name__ == "__main__":
    asyncio.run(test_autonomous_mission())
