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
    
    def _get_redis(self, override_client=None):
        """
        Helper to get the best available Redis client.
        Prioritizes the one passed in the method call (thread-safe), falls back to global.
        """
        return override_client or GlobalResources.redis_client

    async def add_timer(self, timer_data: Dict, redis_client=None) -> str:
        """
        Saves a timer to Redis. 
        Returns the Timer ID on success, or empty string on failure.
        """
        r = self._get_redis(redis_client)
        if not r:
            log.warning("TimerStorage: Redis not available. Timer not saved.")
            return ""
        
        # Generate ID if missing
        timer_id = timer_data.get("id") or str(uuid.uuid4())
        timer_data["id"] = timer_id
        
        key = f"{REDIS_KEY_PREFIX}:{timer_id}"
        
        try:
            r.set(key, json.dumps(timer_data))
            # Optional: Persist for 30 days to prevent infinite junk, or keep indefinite.
            # r.expire(key, 2592000) 
            return timer_id
        except Exception as e:
            log.error(f"TimerStorage: Failed to save timer {timer_id}: {e}")
            return ""

    async def get_timer(self, timer_id: str, redis_client=None) -> Optional[Dict]:
        r = self._get_redis(redis_client)
        if not r: return None
        
        try:
            data = r.get(f"{REDIS_KEY_PREFIX}:{timer_id}")
            return json.loads(data) if data else None
        except Exception as e:
            log.error(f"TimerStorage: Failed to get timer {timer_id}: {e}")
            return None

    async def list_timers(self, redis_client=None) -> List[Dict]:
        """Lists all active and paused timers."""
        r = self._get_redis(redis_client)
        if not r: 
            # This warning helps debug why "List active timers" returns nothing
            log.warning("TimerStorage: Redis not connected during list_timers.")
            return []
        
        try:
            # NOTE: scan_iter is safer for production than keys(), but keys() is fine for <1000 timers
            keys = r.keys(f"{REDIS_KEY_PREFIX}:*")
            timers = []
            for k in keys:
                try:
                    data = r.get(k)
                    if data:
                        t = json.loads(data)
                        timers.append(t)
                except Exception as e:
                    log.error(f"Error loading timer {k}: {e}")
            return timers
        except Exception as e:
            log.error(f"TimerStorage list error: {e}")
            return []

    async def delete_timer(self, timer_id: str, redis_client=None):
        r = self._get_redis(redis_client)
        if not r: return
        try:
            r.delete(f"{REDIS_KEY_PREFIX}:{timer_id}")
        except Exception as e:
            log.error(f"TimerStorage: Failed to delete {timer_id}: {e}")

    async def update_timer(self, timer_id: str, updates: Dict, redis_client=None):
        """Updates specific fields of an existing timer."""
        current = await self.get_timer(timer_id, redis_client)
        if current:
            current.update(updates)
            # Re-save with the same ID
            result = await self.add_timer(current, redis_client)
            return current if result else None
        return None

storage = TimerStorage()
