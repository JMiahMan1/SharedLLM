import httpx
import os
import asyncio
import json

# Configuration
SERVER_IP = "192.168.2.205"
GATEWAY_URL = f"http://{SERVER_IP}:8080" # All frontend calls go here
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")

async def test_frontend_config():
    print("--- Testing Frontend: GET /api/config ---")
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{GATEWAY_URL}/api/config")
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            config = resp.json().get("config", {})
            print(f"Current Config: {json.dumps(config, indent=2)}")
        else:
            print(f"Error: {resp.text}")

async def test_frontend_settings():
    print("\n--- Testing Frontend: GET /api/settings ---")
    async with httpx.AsyncClient() as client:
        # Note: Frontend settings might require internal secret for admin view
        resp = await client.get(f"{GATEWAY_URL}/api/settings", headers={"X-Internal-Secret": INTERNAL_SECRET})
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            settings = resp.json()
            print(f"Total Settings: {len(settings)}")
            if settings:
                print(f"Sample: {settings[0]['key']} = {settings[0]['value']}")
        else:
            print(f"Error: {resp.text}")

async def test_frontend_chat_identity():
    print("\n--- Testing Frontend: POST /api/chat (Identity Pass-through) ---")
    async with httpx.AsyncClient() as client:
        # Test a simple RAG query that triggers identity resolution
        payload = {
            "query": "What are your capabilities?",
            "stream": False,
            "rag_user": "default"
        }
        resp = await client.post(f"{GATEWAY_URL}/api/chat", json=payload)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            print("Chat Success")
        else:
            print(f"Error: {resp.text}")

async def test_frontend_logs():
    print("\n--- Testing Frontend: GET /api/logs ---")
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{GATEWAY_URL}/api/logs?limit=5", headers={"X-Internal-Secret": INTERNAL_SECRET})
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            logs = resp.json()
            print(f"Retrieved {len(logs)} logs")
            if logs:
                print(f"Latest Log: {logs[0].get('message')[:50]}...")
        else:
            print(f"Error: {resp.text}")

async def run_frontend_audit():
    print("=== SharedLLM Frontend API Audit Suite ===")
    try:
        await test_frontend_config()
        await test_frontend_settings()
        await test_frontend_chat_identity()
        await test_frontend_logs()
    except Exception as e:
        print(f"\nAudit failed with exception: {e}")
    print("\n=== Audit Complete ===")

if __name__ == "__main__":
    asyncio.run(run_frontend_audit())
