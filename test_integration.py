import httpx
import os
import asyncio
import json

# Configuration
SERVER_IP = "192.168.2.205"
GATEWAY_URL = f"http://{SERVER_IP}:8080" # Via Caddy
IDENTITY_URL = f"http://{SERVER_IP}:8001"
EXECUTION_URL = f"http://{SERVER_IP}:8003"
RAG_URL = f"http://{SERVER_IP}:8004"
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

async def test_identity_resolution():
    print("--- Testing Identity Resolution ---")
    async with httpx.AsyncClient() as client:
        # 1. Direct Resolution via Internal Secret
        resp = await client.post(
            f"{IDENTITY_URL}/api/resolve",
            json={"rag_user": "default"},
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        print(f"Direct Resolve (default): {resp.status_code}")
        if resp.status_code == 200:
            print(f"Result: {resp.json().get('user')}")
        else:
            print(f"Error: {resp.text}")

async def test_gateway_config():
    print("\n--- Testing Gateway Config Sync ---")
    async with httpx.AsyncClient() as client:
        # 1. Fetch Config
        resp = await client.get(f"{GATEWAY_URL}/api/config")
        print(f"GET /api/config: {resp.status_code}")
        if resp.status_code == 200:
            config = resp.json().get("config", {})
            print(f"Current Coding Model: {config.get('coding_model')}")
            
            # 2. Update Config (to current value to avoid breaking things)
            new_val = config.get("coding_model", "qwen2.5-coder:7b")
            resp = await client.post(
                f"{GATEWAY_URL}/api/config",
                json={"coding_model": new_val},
                headers={"X-Internal-Secret": INTERNAL_SECRET} # Simulate admin/internal update
            )
            print(f"POST /api/config (PATCH sync): {resp.status_code}")
            if resp.status_code == 200:
                print("Update sync SUCCESS")

async def test_execution_trigger():
    print("\n--- Testing Execution Trigger (Hardened Logic) ---")
    async with httpx.AsyncClient() as client:
        # Simulate an automation trigger for a timer
        payload = {
            "user_id": "default",
            "timer": {
                "title": "Integration Test Timer",
                "target_device": "media_player.office_speaker" # Fake device for test
            }
        }
        resp = await client.post(
            f"{EXECUTION_URL}/execute/trigger",
            json=payload,
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        print(f"POST /execute/trigger: {resp.status_code}")
        if resp.status_code == 200:
            print(f"Result: {resp.json().get('message')}")

async def test_rag_isolation():
    print("\n--- Testing RAG Isolation ---")
    async with httpx.AsyncClient() as client:
        # Search for a user
        resp = await client.post(
            f"{RAG_URL}/rag/search",
            json={
                "user_id": "non_existent_user_999",
                "query": "test query",
                "collection_name": "system_capabilities",
                "k": 1
            },
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        print(f"RAG Search (isolated): {resp.status_code}")
        if resp.status_code == 200:
            # Should only find 'default' or nothing, never other users
            hits = resp.json().get("hits", [])
            print(f"Found {len(hits)} hits (correct if only 'default' or none)")

async def run_all_tests():
    print("=== SharedLLM Integration Audit Suite ===")
    try:
        await test_identity_resolution()
        await test_gateway_config()
        await test_execution_trigger()
        await test_rag_isolation()
    except Exception as e:
        print(f"\nAudit failed with exception: {e}")
    print("\n=== Audit Complete ===")

if __name__ == "__main__":
    asyncio.run(run_all_tests())
