import asyncio
import os
import json
import httpx
from messaging import InferenceJobQueue

async def main():
    INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
    IDENTITY_SVC = os.getenv("IDENTITY_SVC_URL", "http://127.0.0.1:8001")
    REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
    
    job_queue = InferenceJobQueue(REDIS_URL)
    await job_queue.connect()
    
    async with httpx.AsyncClient() as client:
        # Get missions
        resp = await client.get(
            f"{IDENTITY_SVC}/api/raven/missions",
            headers={"X-Internal-Secret": INTERNAL_SECRET}
        )
        missions = resp.json()
        
        for m in missions:
            if m["mission_type"] == "user_task" and m["status"] == "pending":
                print(f"Enqueuing stuck mission: {m['id']} - {m['proposed_mission']}")
                
                system_prompt = f"You are Raven, an autonomous agent executing a user-assigned background mission. Execute the following task to the best of your ability:\n{m['proposed_mission']}"
                
                await job_queue.enqueue_job("raven_user", {
                    "query": m["proposed_mission"],
                    "model": m.get("coding_model", "auto"),
                    "system": system_prompt,
                    "stream": False,
                    "creds": {"user_id": "raven_user", "username": "raven", "is_admin": True},
                    "_mission_id": m["id"]
                })
                
                await client.patch(
                    f"{IDENTITY_SVC}/api/raven/missions/{m['id']}",
                    json={"status": "executing"},
                    headers={"X-Internal-Secret": INTERNAL_SECRET}
                )
                
                print(f"Mission {m['id']} successfully enqueued.")

if __name__ == "__main__":
    asyncio.run(main())
