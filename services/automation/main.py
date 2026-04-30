# services/automation/main.py
import asyncio
import logging
import os
import json
import httpx
from datetime import datetime
import redis.asyncio as redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s")
log = logging.getLogger("automation")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
EXECUTION_SVC = os.getenv("EXECUTION_SVC_URL", "http://execution:8003")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "change-me-in-production")
SCHEDULER_INTERVAL = 5 # seconds

redis_client = None

async def scheduler_loop():
    global redis_client
    log.info(f"Automation Scheduler Started. Connecting to Redis: {REDIS_URL}")
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    
    while True:
        try:
            # 1. List Timers from Redis
            # We assume a key pattern 'timer:*'
            keys = await redis_client.keys("timer:*")
            now = datetime.now()
            
            for key in keys:
                timer_data = await redis_client.get(key)
                if not timer_data: continue
                
                t = json.loads(timer_data)
                if not t.get("active", True): continue
                
                expires = datetime.fromisoformat(t["expires_at"])
                # Ensure naive for comparison
                if expires.tzinfo: expires = expires.replace(tzinfo=None)

                if now >= expires:
                    log.info(f"Triggering Timer: {t.get('title')} ({t.get('id')})")
                    # Dispatch to Execution Service for actual audio/action
                    async with httpx.AsyncClient() as client:
                        # We use a special internal endpoint in execution for triggers
                        try:
                            await client.post(
                                f"{EXECUTION_SVC}/execute/trigger",
                                json={"timer": t},
                                headers={"X-Internal-Secret": INTERNAL_SECRET},
                                timeout=10.0
                            )
                            # Remove one-time timer or update recurring
                            if not t.get("recurrence"):
                                await redis_client.delete(key)
                                log.info(f"One-time timer {t.get('id')} deleted.")
                            else:
                                # Logic for recurrence would go here (update expires_at)
                                pass
                        except Exception as e:
                            log.error(f"Failed to trigger timer {t.get('id')}: {e}")
                    
        except Exception as e:
            log.error(f"Scheduler Error: {e}")
            
        await asyncio.sleep(SCHEDULER_INTERVAL)

if __name__ == "__main__":
    asyncio.run(scheduler_loop())
