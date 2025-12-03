# app/logic/timer_storage.py
import json
import uuid
import time
from typing import List, Dict, Optional
from settings import GlobalResources, log

REDIS_KEY_PREFIX = "rag:timers"

class TimerStorage:
    def __init__(self):
        pass
    
    @property
    def redis(self):
        return GlobalResources.redis_client

    async def add_timer(self, timer_data: Dict) -> str:
        """Saves a timer to Redis. Returns the Timer ID."""
        if not self.redis:
            log.warning("TimerStorage: Redis not available. Timer not saved.")
            return ""
        
        timer_id = timer_data.get("id") or str(uuid.uuid4())
        timer_data["id"] = timer_id
        
        key = f"{REDIS_KEY_PREFIX}:{timer_id}"
        self.redis.set(key, json.dumps(timer_data))
        return timer_id

    async def get_timer(self, timer_id: str) -> Optional[Dict]:
        if not self.redis: return None
        data = self.redis.get(f"{REDIS_KEY_PREFIX}:{timer_id}")
        return json.loads(data) if data else None

    async def list_timers(self) -> List[Dict]:
        """Lists all active and paused timers."""
        if not self.redis: return []
        
        # NOTE: Using keys() on a large database can be slow; for a real production system, 
        # SCAN would be preferred, but keys() is simple for this example.
        keys = self.redis.keys(f"{REDIS_KEY_PREFIX}:*")
        timers = []
        for k in keys:
            try:
                data = self.redis.get(k)
                if data:
                    t = json.loads(data)
                    timers.append(t)
            except Exception as e:
                log.error(f"Error loading timer {k}: {e}")
        return timers

    async def delete_timer(self, timer_id: str):
        if not self.redis: return
        self.redis.delete(f"{REDIS_KEY_PREFIX}:{timer_id}")

    async def update_timer(self, timer_id: str, updates: Dict):
        current = await self.get_timer(timer_id)
        if current:
            current.update(updates)
            await self.add_timer(current)
            return current
        return None

storage = TimerStorage()
